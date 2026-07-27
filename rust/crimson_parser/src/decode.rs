//! Stage 2: object-tree decode, a faithful port of save_parser.py.
//!
//! Every heuristic - suffix-first fixed fields, list header probing, the
//! trailing-size search - is reproduced exactly, including Python's value
//! formatting, because parity is proven by hashing both trees into the same
//! canonical stream.

use crate::{Schema, TocEntry, TypeDef};

#[derive(Default)]
pub struct Node {
    pub field_index: usize,
    pub name: String,
    pub type_name: String,
    pub meta_kind: u16,
    pub meta_size: u16,
    pub meta_aux: u32,
    pub present: bool,
    pub decode_kind: &'static str,
    pub start_offset: usize,
    pub end_offset: usize,
    pub value_repr: String,
    pub edit_format: String,
    pub editable: bool,
    pub note: String,
    pub child_prefix_u16: u16,
    pub child_prefix_u8: u8,
    pub child_mask_byte_count: usize,
    pub child_mask_bytes: Vec<u8>,
    pub child_type_index: i64,
    pub child_type_name: String,
    pub child_reserved_u8: u8,
    pub child_sentinel1_u32: u32,
    pub child_sentinel2_u32: u32,
    pub child_payload_offset: usize,
    pub child_reserved_u32: u32,
    pub child_size_u32: u32,
    pub child_fields: Option<Vec<Node>>,
    pub child_undecoded: Option<Vec<(usize, usize)>>,
    pub list_prefix_u8: u8,
    pub list_count: u32,
    pub list_reserved1_u32: u32,
    pub list_reserved2_u32: u32,
    pub list_reserved3_u32: u32,
    pub list_reserved4_u16: u16,
    pub list_reserved4_u32: u32,
    pub list_header_size: usize,
    pub list_elements: Option<Vec<Node>>,
}

impl Node {
    fn from_field(index: usize, f: &crate::FieldDef, present: bool, note: &str) -> Node {
        Node {
            field_index: index,
            name: f.name.clone(),
            type_name: f.type_name.clone(),
            meta_kind: f.meta_kind,
            meta_size: f.meta_size,
            meta_aux: f.meta_aux,
            present,
            decode_kind: if present { "unknown" } else { "absent" },
            note: note.to_string(),
            child_type_index: -1,
            ..Default::default()
        }
    }
}

pub struct Block {
    pub entry_index: usize,
    pub class_index: u32,
    pub class_name: String,
    pub data_offset: usize,
    pub data_size: usize,
    pub mask_byte_count: usize,
    pub header_mask_bytes: Vec<u8>,
    pub reserved_u32: u32,
    pub fields: Vec<Node>,
    pub undecoded: Vec<(usize, usize)>,
}

type DecodeResult<T> = Result<T, ()>;

fn u16le(raw: &[u8], p: usize) -> u16 {
    u16::from_le_bytes([raw[p], raw[p + 1]])
}
fn u32le(raw: &[u8], p: usize) -> u32 {
    u32::from_le_bytes([raw[p], raw[p + 1], raw[p + 2], raw[p + 3]])
}
fn u64le(raw: &[u8], p: usize) -> u64 {
    u64::from_le_bytes(raw[p..p + 8].try_into().unwrap())
}
fn u24le(raw: &[u8], p: usize) -> u32 {
    raw[p] as u32 | ((raw[p + 1] as u32) << 8) | ((raw[p + 2] as u32) << 16)
}
fn u16be(raw: &[u8], p: usize) -> u32 {
    ((raw[p] as u32) << 8) | raw[p + 1] as u32
}

fn hex(data: &[u8]) -> String {
    const TABLE: &[u8; 16] = b"0123456789abcdef";
    let mut out = Vec::with_capacity(data.len() * 2);
    for &b in data {
        out.push(TABLE[(b >> 4) as usize]);
        out.push(TABLE[(b & 0xf) as usize]);
    }
    String::from_utf8(out).unwrap()
}

/// Python float repr: David Gay style - the smallest number of significant
/// digits that round-trips, correctly rounded, then CPython's fixed-or-
/// scientific presentation. Rust's {:?} uses Ryu, which can pick a different
/// final digit when two shortest strings both round-trip.
fn py_float_repr(v: f64) -> String {
    if v.is_nan() {
        return "nan".into();
    }
    if v.is_infinite() {
        return if v > 0.0 { "inf".into() } else { "-inf".into() };
    }
    if v == 0.0 {
        return if v.is_sign_negative() { "-0.0".into() } else { "0.0".into() };
    }

    let mut sci = String::new();
    for precision in 0..=16usize {
        sci = format!("{v:.precision$e}");
        if sci.parse::<f64>() == Ok(v) {
            break;
        }
    }
    // sci looks like -2.7945938110351562e0
    let negative = sci.starts_with('-');
    let body = sci.trim_start_matches('-');
    let (mantissa, exp) = body.split_once('e').unwrap();
    let exp: i32 = exp.parse().unwrap();
    let digits: String = mantissa.chars().filter(|c| *c != '.').collect();
    let digits = digits.trim_end_matches('0');
    let digits = if digits.is_empty() { "0" } else { digits };
    let sign = if negative { "-" } else { "" };

    if (-4..16).contains(&exp) {
        let mut out = String::new();
        if exp >= 0 {
            let int_len = (exp as usize) + 1;
            if digits.len() > int_len {
                out.push_str(&digits[..int_len]);
                out.push('.');
                out.push_str(&digits[int_len..]);
            } else {
                out.push_str(digits);
                out.push_str(&"0".repeat(int_len - digits.len()));
                out.push_str(".0");
            }
        } else {
            out.push_str("0.");
            out.push_str(&"0".repeat((-exp - 1) as usize));
            out.push_str(digits);
        }
        format!("{sign}{out}")
    } else {
        let mantissa_out = if digits.len() > 1 {
            format!("{}.{}", &digits[..1], &digits[1..])
        } else {
            digits.to_string()
        };
        let exp_sign = if exp < 0 { "-" } else { "+" };
        format!("{sign}{mantissa_out}e{exp_sign}{:02}", exp.abs())
    }
}

/// Python str repr for ASCII-with-replacement decoded bytes.
fn py_str_repr(decoded: &str) -> String {
    let has_single = decoded.contains('\'');
    let has_double = decoded.contains('"');
    let quote = if has_single && !has_double { '"' } else { '\'' };
    let mut out = String::with_capacity(decoded.len() + 2);
    out.push(quote);
    for ch in decoded.chars() {
        match ch {
            '\\' => out.push_str("\\\\"),
            '\t' => out.push_str("\\t"),
            '\r' => out.push_str("\\r"),
            '\n' => out.push_str("\\n"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            c if (c as u32) < 0x20 || (c as u32) == 0x7f => {
                out.push_str(&format!("\\x{:02x}", c as u32));
            }
            c => out.push(c),
        }
    }
    out.push(quote);
    out
}

fn ascii_replace(data: &[u8]) -> String {
    data.iter()
        .map(|&b| if b < 0x80 { b as char } else { '\u{fffd}' })
        .collect()
}

fn type_to_edit_format(type_name: &str, size: u16) -> &'static str {
    let lower = type_name.to_ascii_lowercase();
    if lower == "bool" {
        return "bool";
    }
    if lower.contains("float") && size == 4 {
        return "<f";
    }
    if lower.contains("float") && size == 8 {
        return "<d";
    }
    if lower.starts_with("int") {
        return match size {
            1 => "<b",
            2 => "<h",
            4 => "<i",
            8 => "<q",
            _ => "",
        };
    }
    match size {
        1 => "<B",
        2 => "<H",
        4 => "<I",
        8 => "<Q",
        _ => "",
    }
}

fn field_present(mask: &[u8], index: usize) -> bool {
    let byte = index / 8;
    if byte >= mask.len() {
        return false;
    }
    (mask[byte] & (1 << (index % 8))) != 0
}

/// Mirror of _decode_fixed_value. Returns (end, value_repr, edit_format, editable).
fn decode_fixed_value(
    raw: &[u8],
    offset: usize,
    f: &crate::FieldDef,
) -> (usize, String, String, bool) {
    let size = f.meta_size as usize;
    let end = offset + size;
    let data = &raw[offset..end];
    let edit_format = type_to_edit_format(&f.type_name, f.meta_size);
    let editable = !edit_format.is_empty();
    let lower = f.type_name.to_ascii_lowercase();

    if lower == "bool" && size == 1 {
        let repr = if data[0] != 0 { "true" } else { "false" };
        return (end, repr.into(), "bool".into(), true);
    }
    match edit_format {
        "<f" => {
            let v = f32::from_le_bytes(data.try_into().unwrap()) as f64;
            (end, py_float_repr(v), "<f".into(), true)
        }
        "<d" => {
            let v = f64::from_le_bytes(data.try_into().unwrap());
            (end, py_float_repr(v), "<d".into(), true)
        }
        "<b" => (end, (data[0] as i8).to_string(), "<b".into(), editable),
        "<h" => (
            end,
            i16::from_le_bytes(data.try_into().unwrap()).to_string(),
            "<h".into(),
            editable,
        ),
        "<i" => (
            end,
            i32::from_le_bytes(data.try_into().unwrap()).to_string(),
            "<i".into(),
            editable,
        ),
        "<q" => (
            end,
            i64::from_le_bytes(data.try_into().unwrap()).to_string(),
            "<q".into(),
            editable,
        ),
        "<B" => (end, data[0].to_string(), "<B".into(), editable),
        "<H" => (end, u16le(raw, offset).to_string(), "<H".into(), editable),
        "<I" => (end, u32le(raw, offset).to_string(), "<I".into(), editable),
        "<Q" => (end, u64le(raw, offset).to_string(), "<Q".into(), editable),
        _ => (end, hex(data), String::new(), false),
    }
}

/// Mirror of _decode_inline_bytes. Returns (end, value_repr, note).
fn decode_inline_bytes(
    raw: &[u8],
    offset: usize,
    f: &crate::FieldDef,
    tail: usize,
) -> DecodeResult<(usize, String, String)> {
    if offset + 4 > tail {
        return Err(());
    }
    let count = u32le(raw, offset) as usize;
    let total = 4 + count * f.meta_size as usize;
    let end = offset + total;
    if end > tail {
        return Err(());
    }
    let data = &raw[offset + 4..end];
    let lower = f.type_name.to_ascii_lowercase();
    let value_repr = if f.meta_size == 1 && (lower.contains("string") || lower.ends_with('a')) {
        let trimmed: &[u8] = {
            let mut t = data;
            while let Some((&0, rest)) = t.split_last() {
                t = rest;
            }
            t
        };
        py_str_repr(&ascii_replace(trimmed))
    } else if f.meta_size == 1 {
        hex(data)
    } else {
        let preview = hex(&data[..data.len().min(32)]);
        format!("bytes={} preview={preview}", data.len())
    };
    Ok((end, value_repr, format!("count={count}")))
}

/// Mirror of _decode_dynamic_array. Returns (end, value_repr, note).
fn decode_dynamic_array(
    raw: &[u8],
    offset: usize,
    f: &crate::FieldDef,
    tail: usize,
) -> DecodeResult<(usize, String, String)> {
    let meta = f.meta_size as usize;
    if offset + 5 > tail {
        return Err(());
    }
    if offset + 14 <= tail && raw[offset..offset + 5] == [0, 0, 6, 1, 0] {
        let count = u32le(raw, offset + 5) as usize;
        let total = 9 + count * meta + 5;
        let end = offset + total;
        if count < 0x10000 && end <= tail && raw[end - 5..end] == [1, 1, 1, 1, 1] {
            let data = &raw[offset + 9..offset + 9 + count * meta];
            let preview = hex(&data[..data.len().min(16)]);
            return Ok((
                end,
                format!("count={count} bytes={} preview={preview}", data.len()),
                "dynamic primitive array (prefix 0000060100)".into(),
            ));
        }
    }
    if raw[offset] == 1 && offset + 7 <= tail {
        let mut marker_end = offset;
        while marker_end < tail && raw[marker_end] == 1 {
            marker_end += 1;
        }
        if marker_end > offset
            && marker_end < tail
            && raw[marker_end] == 0
            && marker_end + 5 <= tail
        {
            let count = u32le(raw, marker_end + 1) as usize;
            let total = (marker_end - offset + 1) + 4 + count * meta;
            let mut end = offset + total;
            if count < 0x10000 && end <= tail {
                if end < tail && raw[end] == 1 {
                    end += 1;
                }
                let data_offset = marker_end + 5;
                let data = &raw[data_offset..data_offset + count * meta];
                let preview = hex(&data[..data.len().min(16)]);
                return Ok((
                    end,
                    format!("count={count} bytes={} preview={preview}", data.len()),
                    format!(
                        "dynamic primitive array (marker prefix len={})",
                        marker_end - offset
                    ),
                ));
            }
        }
    }
    let (count, total, note, compact) = if offset + 6 <= tail
        && raw[offset] == 0
        && raw[offset + 1] == 0
        && raw[offset + 4] == 0
        && raw[offset + 5] == 0
    {
        let count = u16le(raw, offset + 2) as usize;
        (
            count,
            6 + count * meta,
            "dynamic primitive array (compact header)".to_string(),
            true,
        )
    } else {
        let prefix = raw[offset];
        let count = u32le(raw, offset + 1) as usize;
        let mut note = "dynamic primitive array".to_string();
        if prefix != 0 {
            note.push_str(&format!(" prefix=0x{prefix:02X}"));
        }
        (count, 1 + 4 + count * meta, note, false)
    };
    let end = offset + total;
    if end > tail {
        return Err(());
    }
    let data_offset = offset + if compact { 6 } else { 5 };
    let data = &raw[data_offset..end];
    let preview = hex(&data[..data.len().min(16)]);
    Ok((
        end,
        format!("count={count} bytes={} preview={preview}", data.len()),
        note,
    ))
}

fn compute_undecoded(
    _block_start: usize,
    block_end: usize,
    header_end: usize,
    fields: &[Node],
) -> Vec<(usize, usize)> {
    let mut decoded: Vec<(usize, usize)> = fields
        .iter()
        .filter(|f| f.start_offset < f.end_offset)
        .map(|f| (f.start_offset, f.end_offset))
        .collect();
    decoded.sort_by_key(|&(s, _)| s);
    let mut spans = Vec::new();
    let mut cursor = header_end;
    for (start, end) in decoded {
        if cursor < start {
            spans.push((cursor, start));
        }
        cursor = cursor.max(end);
    }
    if cursor < block_end {
        spans.push((cursor, block_end));
    }
    spans
}

/// Mirror of _decode_inline_object_payload.
fn decode_inline_object_payload(
    raw: &[u8],
    type_def: &TypeDef,
    mask: &[u8],
    payload_start: usize,
    tail: usize,
    schema: &Schema,
    use_types: bool,
) -> DecodeResult<(usize, u32, u32, Vec<Node>)> {
    if payload_start + 8 > tail {
        return Err(());
    }
    let reserved_u32 = u32le(raw, payload_start);
    let mut cursor = payload_start + 4;
    let mut fields: Vec<Node> = Vec::with_capacity(type_def.fields.len());

    for (index, f) in type_def.fields.iter().enumerate() {
        let present = field_present(mask, index);
        let mut target = Node::from_field(index, f, present, "");
        if !present {
            target.decode_kind = "absent";
            fields.push(target);
            continue;
        }
        if (f.meta_kind == 0 || f.meta_kind == 2) && f.meta_size > 0 {
            if cursor + f.meta_size as usize > tail {
                return Err(());
            }
            let (end, repr, fmt, editable) = decode_fixed_value(raw, cursor, f);
            target.decode_kind = "fixed_prefix";
            target.start_offset = cursor;
            target.end_offset = end;
            target.value_repr = repr;
            target.edit_format = fmt;
            target.editable = editable;
            cursor = end;
            fields.push(target);
            continue;
        }
        if f.meta_kind == 1 && f.meta_size > 0 {
            let (end, repr, note) = decode_inline_bytes(raw, cursor, f, tail)?;
            target.decode_kind = "inline_bytes";
            target.start_offset = cursor;
            target.end_offset = end;
            target.value_repr = repr;
            target.note = note;
            cursor = end;
            fields.push(target);
            continue;
        }
        if f.meta_kind == 3 && f.meta_size > 0 {
            let (end, repr, note) = decode_dynamic_array(raw, cursor, f, tail)?;
            target.decode_kind = "dynamic_array";
            target.start_offset = cursor;
            target.end_offset = end;
            target.value_repr = repr;
            target.note = note;
            cursor = end;
            fields.push(target);
            continue;
        }
        if f.meta_kind == 4 || f.meta_kind == 5 {
            let (end, mut locator) =
                decode_inline_object_locator(raw, cursor, tail, schema, use_types, f.meta_kind)?;
            locator.field_index = index;
            locator.name = f.name.clone();
            locator.type_name = f.type_name.clone();
            locator.meta_kind = f.meta_kind;
            locator.meta_size = f.meta_size;
            locator.meta_aux = f.meta_aux;
            cursor = end;
            fields.push(locator);
            continue;
        }
        if f.meta_kind == 6 || f.meta_kind == 7 {
            let (end, mut list_field) = decode_object_list(raw, cursor, tail, schema)?;
            list_field.field_index = index;
            list_field.name = f.name.clone();
            list_field.type_name = f.type_name.clone();
            list_field.meta_kind = f.meta_kind;
            list_field.meta_size = f.meta_size;
            list_field.meta_aux = f.meta_aux;
            cursor = end;
            fields.push(list_field);
            continue;
        }
        return Err(());
    }

    let max_probe = tail.checked_sub(4).ok_or(())?;
    let mut size_field_offset: Option<usize> = None;
    for probe in cursor..=max_probe {
        if u32le(raw, probe) as usize == probe - payload_start {
            size_field_offset = Some(probe);
            break;
        }
    }
    let size_field_offset = size_field_offset.ok_or(())?;
    let size_u32 = u32le(raw, size_field_offset);
    Ok((size_field_offset + 4, reserved_u32, size_u32, fields))
}

fn lookup_type<'a>(schema: &'a Schema, index: usize, use_types: bool) -> Option<&'a TypeDef> {
    if !use_types {
        return None;
    }
    schema.types.get(index)
}

/// Mirror of _decode_inline_object_locator.
fn decode_inline_object_locator(
    raw: &[u8],
    cursor: usize,
    tail: usize,
    schema: &Schema,
    use_types: bool,
    locator_kind: u16,
) -> DecodeResult<(usize, Node)> {
    let mut prefix_u16: u16 = 0;
    let mut prefix_u8: u8 = 0;
    let mut body_cursor = cursor;
    if locator_kind == 5 {
        if cursor + 1 > tail {
            return Err(());
        }
        let mut found = None;
        for delta in 0..=8usize {
            let probe = cursor + delta;
            if probe + 2 > tail {
                continue;
            }
            let count = u16le(raw, probe) as usize;
            if count > 0 && count <= 16 {
                found = Some(probe);
                break;
            }
        }
        body_cursor = found.ok_or(())?;
        let prefix = &raw[cursor..body_cursor];
        if prefix.len() >= 2 {
            prefix_u16 = prefix[0] as u16 | ((prefix[1] as u16) << 8);
        } else if prefix.len() == 1 {
            prefix_u16 = prefix[0] as u16;
        }
        if prefix.len() >= 3 {
            prefix_u8 = prefix[2];
        }
    }
    if body_cursor + 18 > tail {
        return Err(());
    }
    let mask_count = u16le(raw, body_cursor) as usize;
    if mask_count == 0 || mask_count > 16 {
        return Err(());
    }
    let wrapper_end = body_cursor + 2 + mask_count + 2 + 1 + 4 + 4 + 4;
    if wrapper_end > tail {
        return Err(());
    }
    let mask_bytes = raw[body_cursor + 2..body_cursor + 2 + mask_count].to_vec();
    let child_type_index = u16le(raw, body_cursor + 2 + mask_count) as usize;
    let child_reserved_u8 = raw[body_cursor + 2 + mask_count + 2];
    let sentinel1 = u32le(raw, body_cursor + 2 + mask_count + 3);
    let sentinel2 = u32le(raw, body_cursor + 2 + mask_count + 7);
    let payload_offset = u32le(raw, body_cursor + 2 + mask_count + 11) as usize;
    let child_type_def = lookup_type(schema, child_type_index, use_types);
    let child_type_name = child_type_def
        .map(|t| t.name.clone())
        .unwrap_or_else(|| format!("<type {child_type_index}>"));

    let mut end = wrapper_end;
    let mut node = Node {
        meta_kind: locator_kind,
        present: true,
        decode_kind: "object_locator",
        start_offset: cursor,
        end_offset: end,
        value_repr: format!(
            "type={child_type_name} mask={} target=0x{payload_offset:X}",
            hex(&mask_bytes)
        ),
        child_prefix_u16: prefix_u16,
        child_prefix_u8: prefix_u8,
        child_mask_byte_count: mask_count,
        child_mask_bytes: mask_bytes.clone(),
        child_type_index: child_type_index as i64,
        child_type_name,
        child_reserved_u8,
        child_sentinel1_u32: sentinel1,
        child_sentinel2_u32: sentinel2,
        child_payload_offset: payload_offset,
        ..Default::default()
    };

    if payload_offset == wrapper_end {
        if let Some(td) = child_type_def {
            let (child_end, reserved, size, children) = decode_inline_object_payload(
                raw,
                td,
                &mask_bytes,
                payload_offset,
                tail,
                schema,
                use_types,
            )?;
            node.child_reserved_u32 = reserved;
            node.child_size_u32 = size;
            node.child_fields = Some(children);
            node.child_undecoded = Some(Vec::new());
            end = child_end;
            node.end_offset = child_end;
        } else {
            let cap = (tail.saturating_sub(4)).min(payload_offset + 512);
            for probe in payload_offset..=cap {
                let size = u32le(raw, probe) as usize;
                if size == probe - payload_offset && size > 0 {
                    end = probe + 4;
                    node.end_offset = end;
                    node.child_size_u32 = size as u32;
                    break;
                }
            }
        }
    }
    Ok((end, node))
}

/// Mirror of _decode_compact_list_element (post NameError fix).
fn decode_compact_list_element(
    raw: &[u8],
    cursor: usize,
    tail: usize,
    schema: &Schema,
    use_types: bool,
) -> DecodeResult<(usize, Node)> {
    if cursor + 18 > tail {
        return Err(());
    }
    let mask_count = u16le(raw, cursor) as usize;
    if !(1..=16).contains(&mask_count) {
        return Err(());
    }
    let header_size = 2 + mask_count + 2 + 1 + 8 + 4;
    if cursor + header_size > tail {
        return Err(());
    }
    let mask_bytes = raw[cursor + 2..cursor + 2 + mask_count].to_vec();
    let child_type_index = u16le(raw, cursor + 2 + mask_count) as usize;
    let child_reserved_u8 = raw[cursor + 2 + mask_count + 2];
    let sentinel_offset = cursor + 2 + mask_count + 3;
    if u64le(raw, sentinel_offset) != u64::MAX {
        return Err(());
    }
    let payload_offset = u32le(raw, sentinel_offset + 8) as usize;
    let td = lookup_type(schema, child_type_index, use_types).ok_or(())?;
    if payload_offset != cursor + header_size {
        return Err(());
    }
    let (child_end, reserved, size, children) =
        decode_inline_object_payload(raw, td, &mask_bytes, payload_offset, tail, schema, use_types)?;
    let node = Node {
        meta_kind: 6,
        present: true,
        decode_kind: "object_locator",
        start_offset: cursor,
        end_offset: child_end,
        value_repr: format!("type={} target=0x{payload_offset:X}", td.name),
        child_prefix_u16: 0,
        child_prefix_u8: 0,
        child_mask_byte_count: mask_count,
        child_mask_bytes: mask_bytes,
        child_type_index: child_type_index as i64,
        child_type_name: td.name.clone(),
        child_reserved_u8,
        child_sentinel1_u32: 0xFFFF_FFFF,
        child_sentinel2_u32: 0xFFFF_FFFF,
        child_payload_offset: payload_offset,
        child_reserved_u32: reserved,
        child_size_u32: size,
        child_fields: Some(children),
        child_undecoded: Some(Vec::new()),
        note: "compact_list_element".into(),
        ..Default::default()
    };
    Ok((child_end, node))
}

fn decode_object_list_element(
    raw: &[u8],
    cursor: usize,
    tail: usize,
    schema: &Schema,
    use_types: bool,
) -> DecodeResult<(usize, Node)> {
    if let Ok(hit) = decode_inline_object_locator(raw, cursor, tail, schema, use_types, 4) {
        return Ok(hit);
    }
    decode_compact_list_element(raw, cursor, tail, schema, use_types)
}

/// Mirror of _decode_object_list.
fn decode_object_list(
    raw: &[u8],
    cursor: usize,
    tail: usize,
    schema: &Schema,
) -> DecodeResult<(usize, Node)> {
    if cursor + 18 > tail {
        return Err(());
    }
    let mut best: Option<(usize, Node)> = None;
    for delta in 0..4usize {
        let body_cursor = cursor + delta;
        if body_cursor + 18 > tail {
            continue;
        }
        let attempt = (|| -> DecodeResult<(usize, Node)> {
            let prefix_u8 = raw[body_cursor];
            let mut marker_end = body_cursor;
            while marker_end < tail && raw[marker_end] == 1 {
                marker_end += 1;
            }
            let (count, r1, r2, r3, r4_16, r4_32, header_size): (u32, u32, u32, u32, u16, u32, usize);
            if marker_end > body_cursor
                && marker_end + 17 <= tail
                && raw[marker_end] == 0
                && raw[marker_end + 5..marker_end + 18].iter().all(|&b| b == 0)
            {
                count = u32le(raw, marker_end + 1);
                r1 = 0;
                r2 = 0;
                r3 = 0;
                r4_16 = 0;
                r4_32 = 0;
                header_size = (marker_end - body_cursor + 1) + 4 + 13;
            } else if prefix_u8 == 0
                && raw[body_cursor + 1] == 0
                && raw[body_cursor + 2] == 0
                && raw[body_cursor + 3] == 0
            {
                count = u32le(raw, body_cursor + 4);
                r1 = 0;
                r2 = u32le(raw, body_cursor + 8);
                r3 = u32le(raw, body_cursor + 12);
                r4_16 = u16le(raw, body_cursor + 16);
                r4_32 = 0;
                header_size = 18;
            } else if prefix_u8 == 0 {
                count = u24le(raw, body_cursor + 1);
                r1 = u32le(raw, body_cursor + 4);
                r2 = u32le(raw, body_cursor + 8);
                r3 = u32le(raw, body_cursor + 12);
                r4_16 = u16le(raw, body_cursor + 16);
                r4_32 = 0;
                header_size = 18;
            } else if prefix_u8 == 1
                && body_cursor + 21 <= tail
                && raw[body_cursor + 1] == 1
                && raw[body_cursor + 2] == 1
                && raw[body_cursor + 3] == 0
            {
                count = u32le(raw, body_cursor + 4);
                r1 = u32le(raw, body_cursor + 8);
                r2 = u32le(raw, body_cursor + 12);
                r3 = u32le(raw, body_cursor + 16);
                r4_16 = 0;
                r4_32 = 0;
                header_size = 21;
            } else if prefix_u8 == 1 {
                if body_cursor + 19 > tail {
                    return Err(());
                }
                count = u16be(raw, body_cursor + 1);
                r1 = u32le(raw, body_cursor + 3);
                r2 = u32le(raw, body_cursor + 7);
                r3 = u32le(raw, body_cursor + 11);
                r4_16 = 0;
                r4_32 = u32le(raw, body_cursor + 15);
                header_size = 19;
            } else {
                return Err(());
            }

            let mut element_cursor = body_cursor + header_size;
            let mut elements = Vec::new();
            for index in 0..count as usize {
                let hit = decode_object_list_element(raw, element_cursor, tail, schema, true)
                    .or_else(|_| {
                        decode_object_list_element(raw, element_cursor, tail, schema, false)
                    });
                let (end, mut element) = match hit {
                    Ok(v) => v,
                    Err(()) => break,
                };
                element.field_index = index;
                element.name = format!("[{index}]");
                element.type_name = element.child_type_name.clone();
                element.decode_kind = "list_element";
                elements.push(element);
                element_cursor = end;
            }
            let node = Node {
                meta_kind: 6,
                present: true,
                decode_kind: "object_list",
                start_offset: cursor,
                end_offset: element_cursor,
                value_repr: format!("prefix={prefix_u8} count={count}"),
                list_prefix_u8: prefix_u8,
                list_count: count,
                list_reserved1_u32: r1,
                list_reserved2_u32: r2,
                list_reserved3_u32: r3,
                list_reserved4_u16: r4_16,
                list_reserved4_u32: r4_32,
                list_header_size: if count == 0 {
                    element_cursor - cursor
                } else {
                    (body_cursor - cursor) + header_size
                },
                // GenericFieldValue defaults child_type_index to -1.
                child_type_index: -1,
                list_elements: Some(elements),
                note: if body_cursor != cursor {
                    format!("header_offset=+{}", body_cursor - cursor)
                } else {
                    String::new()
                },
                ..Default::default()
            };
            Ok((element_cursor, node))
        })();
        if let Ok(result) = attempt {
            if best.as_ref().map_or(true, |b| result.0 > b.0) {
                best = Some(result);
            }
        }
    }
    best.ok_or(())
}

/// Mirror of _decode_fields_in_region.
fn decode_fields_in_region(
    raw: &[u8],
    type_def: &TypeDef,
    mask: &[u8],
    region_start: usize,
    region_end: usize,
    schema: &Schema,
    note: &str,
) -> (Vec<Node>, Vec<(usize, usize)>) {
    let mut fields: Vec<Node> = type_def
        .fields
        .iter()
        .enumerate()
        .map(|(i, f)| Node::from_field(i, f, field_present(mask, i), note))
        .collect();

    let mut tail = region_end;
    for index in (0..type_def.fields.len()).rev() {
        let f = &type_def.fields[index];
        if !fields[index].present {
            fields[index].decode_kind = "absent";
            continue;
        }
        if f.meta_kind != 0 && f.meta_kind != 2 {
            break;
        }
        let size = f.meta_size as usize;
        if size == 0 || tail < region_start + size {
            break;
        }
        let start = tail - size;
        let (end, repr, fmt, editable) = decode_fixed_value(raw, start, f);
        if end != tail {
            break;
        }
        let target = &mut fields[index];
        target.decode_kind = "fixed_suffix";
        target.start_offset = start;
        target.end_offset = end;
        target.value_repr = repr;
        target.edit_format = fmt;
        target.editable = editable;
        tail = start;
    }

    let mut head = region_start;
    for index in 0..type_def.fields.len() {
        let f = &type_def.fields[index];
        if !fields[index].present {
            fields[index].decode_kind = "absent";
            continue;
        }
        if fields[index].start_offset < fields[index].end_offset {
            continue;
        }
        if head >= tail {
            break;
        }
        if (f.meta_kind == 0 || f.meta_kind == 2) && f.meta_size > 0 {
            if head + f.meta_size as usize > tail {
                break;
            }
            let (end, repr, fmt, editable) = decode_fixed_value(raw, head, f);
            let target = &mut fields[index];
            target.decode_kind = "fixed_prefix";
            target.start_offset = head;
            target.end_offset = end;
            target.value_repr = repr;
            target.edit_format = fmt;
            target.editable = editable;
            head = end;
            continue;
        }
        if f.meta_kind == 1 && f.meta_size > 0 {
            let Ok((end, repr, note_text)) = decode_inline_bytes(raw, head, f, tail) else {
                break;
            };
            let target = &mut fields[index];
            target.decode_kind = "inline_bytes";
            target.start_offset = head;
            target.end_offset = end;
            target.value_repr = repr;
            target.note = note_text;
            head = end;
            continue;
        }
        if f.meta_kind == 3 && f.meta_size > 0 {
            let Ok((end, repr, note_text)) = decode_dynamic_array(raw, head, f, tail) else {
                break;
            };
            let target = &mut fields[index];
            target.decode_kind = "dynamic_array";
            target.start_offset = head;
            target.end_offset = end;
            target.value_repr = repr;
            target.note = note_text;
            head = end;
            continue;
        }
        if f.meta_kind == 4 || f.meta_kind == 5 {
            let Ok((end, mut locator)) =
                decode_inline_object_locator(raw, head, tail, schema, true, f.meta_kind)
            else {
                break;
            };
            locator.field_index = index;
            locator.name = f.name.clone();
            locator.type_name = f.type_name.clone();
            locator.meta_kind = f.meta_kind;
            locator.meta_size = f.meta_size;
            locator.meta_aux = f.meta_aux;
            fields[index] = locator;
            head = end;
            continue;
        }
        if f.meta_kind == 6 || f.meta_kind == 7 {
            let Ok((end, mut list_field)) = decode_object_list(raw, head, tail, schema) else {
                break;
            };
            list_field.field_index = index;
            list_field.name = f.name.clone();
            list_field.type_name = f.type_name.clone();
            list_field.meta_kind = f.meta_kind;
            list_field.meta_size = f.meta_size;
            list_field.meta_aux = f.meta_aux;
            fields[index] = list_field;
            head = end;
            continue;
        }
        break;
    }

    let undecoded = compute_undecoded(region_start, region_end, region_start, &fields);
    (fields, undecoded)
}

/// Mirror of decode_object_blocks.
pub fn decode_blocks(raw: &[u8], schema: &Schema, entries: &[TocEntry]) -> Vec<Block> {
    let mut blocks = Vec::new();
    for entry in entries {
        let Some(type_def) = schema.types.get(entry.class_index as usize) else {
            continue;
        };
        let block_start = entry.data_offset as usize;
        let block_end = raw.len().min(block_start + entry.data_size as usize);
        if block_end <= block_start {
            continue;
        }
        let expected_mask = ((type_def.fields.len() + 7) / 8).max(1);
        if block_start + 2 > block_end {
            continue;
        }
        let actual_mask = u16le(raw, block_start) as usize;
        let mut mask_count = expected_mask;
        if actual_mask > 0 && actual_mask <= 16 {
            mask_count = actual_mask;
        }
        let note = if mask_count != expected_mask {
            format!("expected_mask_bytes={expected_mask}")
        } else {
            String::new()
        };
        let header_end = block_start + 2 + mask_count + 4;
        if header_end > block_end {
            continue;
        }
        let mask_bytes = raw[block_start + 2..block_start + 2 + mask_count].to_vec();
        let reserved_u32 = u32le(raw, block_start + 2 + mask_count);
        let (fields, undecoded) = decode_fields_in_region(
            raw,
            type_def,
            &mask_bytes,
            header_end,
            block_end,
            schema,
            &note,
        );
        blocks.push(Block {
            entry_index: entry.index,
            class_index: entry.class_index,
            class_name: entry.class_name.clone(),
            data_offset: entry.data_offset as usize,
            data_size: entry.data_size as usize,
            mask_byte_count: mask_count,
            header_mask_bytes: mask_bytes,
            reserved_u32,
            fields,
            undecoded,
        });
    }
    blocks
}

/// FNV-1a over the canonical node stream; the Python side mirrors this walk.
pub struct Digest {
    state: u64,
    pub nodes: u64,
}

impl Digest {
    pub fn new() -> Digest {
        Digest {
            state: 0xcbf2_9ce4_8422_2325,
            nodes: 0,
        }
    }
    fn push(&mut self, data: &[u8]) {
        for &b in data {
            self.state ^= b as u64;
            self.state = self.state.wrapping_mul(0x0000_0100_0000_01b3);
        }
        self.state ^= 0x1f;
        self.state = self.state.wrapping_mul(0x0000_0100_0000_01b3);
    }
    fn text(&mut self, s: &str) {
        self.push(s.as_bytes());
    }
    fn num(&mut self, v: i128) {
        self.push(v.to_string().as_bytes());
    }

    pub fn node(&mut self, n: &Node) {
        self.nodes += 1;
        self.num(n.field_index as i128);
        self.text(&n.name);
        self.text(&n.type_name);
        self.num(n.meta_kind as i128);
        self.num(n.meta_size as i128);
        self.num(n.meta_aux as i128);
        self.num(n.present as i128);
        self.text(n.decode_kind);
        self.num(n.start_offset as i128);
        self.num(n.end_offset as i128);
        self.text(&n.value_repr);
        self.text(&n.edit_format);
        self.num(n.editable as i128);
        self.text(&n.note);
        self.num(n.child_prefix_u16 as i128);
        self.num(n.child_prefix_u8 as i128);
        self.num(n.child_mask_byte_count as i128);
        self.text(&hex(&n.child_mask_bytes));
        self.num(n.child_type_index as i128);
        self.text(&n.child_type_name);
        self.num(n.child_reserved_u8 as i128);
        self.num(n.child_sentinel1_u32 as i128);
        self.num(n.child_sentinel2_u32 as i128);
        self.num(n.child_payload_offset as i128);
        self.num(n.child_reserved_u32 as i128);
        self.num(n.child_size_u32 as i128);
        self.num(n.list_prefix_u8 as i128);
        self.num(n.list_count as i128);
        self.num(n.list_reserved1_u32 as i128);
        self.num(n.list_reserved2_u32 as i128);
        self.num(n.list_reserved3_u32 as i128);
        self.num(n.list_reserved4_u16 as i128);
        self.num(n.list_reserved4_u32 as i128);
        self.num(n.list_header_size as i128);
        match &n.child_fields {
            None => self.text("|nochildren"),
            Some(children) => {
                self.num(children.len() as i128);
                for child in children {
                    self.node(child);
                }
            }
        }
        match &n.child_undecoded {
            None => self.text("|nounranges"),
            Some(spans) => {
                for (a, b) in spans {
                    self.num(*a as i128);
                    self.num(*b as i128);
                }
            }
        }
        match &n.list_elements {
            None => self.text("|noelements"),
            Some(elements) => {
                self.num(elements.len() as i128);
                for element in elements {
                    self.node(element);
                }
            }
        }
    }

    pub fn block(&mut self, b: &Block) {
        self.num(b.entry_index as i128);
        self.num(b.class_index as i128);
        self.text(&b.class_name);
        self.num(b.data_offset as i128);
        self.num(b.data_size as i128);
        self.num(b.mask_byte_count as i128);
        self.text(&hex(&b.header_mask_bytes));
        self.num(b.reserved_u32 as i128);
        for f in &b.fields {
            self.node(f);
        }
        for (a, z) in &b.undecoded {
            self.num(*a as i128);
            self.num(*z as i128);
        }
    }

    pub fn hex(&self) -> String {
        format!("{:016x}", self.state)
    }
}

/// One line per node in walk order, canonical fields joined by 0x1f.
pub fn dump_block_lines(block: &Block) -> Vec<String> {
    fn walk(n: &Node, depth: usize, out: &mut Vec<String>) {
        out.push(format!(
            "{}|{}|{}|{}|k{}|s{}|a{}|p{}|{}|{}..{}|v={}|f={}|e{}|n={}|cp{}#{}|cm{}#{}|ct{}={}|cr{}|s1:{}|s2:{}|po{}|cru{}|cs{}|lp{}|lc{}|lr{}:{}:{}:{}:{}|lh{}",
            depth, n.field_index, n.name, n.type_name, n.meta_kind, n.meta_size,
            n.meta_aux, n.present as u8, n.decode_kind, n.start_offset, n.end_offset,
            n.value_repr, n.edit_format, n.editable as u8, n.note,
            n.child_prefix_u16, n.child_prefix_u8,
            n.child_mask_byte_count, hex(&n.child_mask_bytes),
            n.child_type_index, n.child_type_name, n.child_reserved_u8,
            n.child_sentinel1_u32, n.child_sentinel2_u32, n.child_payload_offset,
            n.child_reserved_u32, n.child_size_u32,
            n.list_prefix_u8, n.list_count, n.list_reserved1_u32,
            n.list_reserved2_u32, n.list_reserved3_u32, n.list_reserved4_u16,
            n.list_reserved4_u32, n.list_header_size,
        ));
        if let Some(children) = &n.child_fields {
            for child in children {
                walk(child, depth + 1, out);
            }
        }
        if let Some(elements) = &n.list_elements {
            for element in elements {
                walk(element, depth + 1, out);
            }
        }
    }
    let mut out = Vec::new();
    for f in &block.fields {
        walk(f, 0, &mut out);
    }
    for (a, b) in &block.undecoded {
        out.push(format!("undecoded {a}..{b}"));
    }
    out
}
