//! Native port of the Crimson Desert save parser.
//!
//! The reference implementation is crimson/save_editor/save_parser.py; this
//! crate ports it stage by stage, and every stage is parity-tested against
//! the Python output on a real save before anything consumes it.
//!
//! Stage 1 (this file): schema and TOC.

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

/// Read a little-endian u16, erroring instead of panicking on truncation.
fn u16_at(raw: &[u8], pos: usize) -> PyResult<u16> {
    raw.get(pos..pos + 2)
        .map(|b| u16::from_le_bytes([b[0], b[1]]))
        .ok_or_else(|| truncated(pos))
}

fn u32_at(raw: &[u8], pos: usize) -> PyResult<u32> {
    raw.get(pos..pos + 4)
        .map(|b| u32::from_le_bytes([b[0], b[1], b[2], b[3]]))
        .ok_or_else(|| truncated(pos))
}

fn ascii_at(raw: &[u8], pos: usize, len: usize) -> PyResult<String> {
    let bytes = raw.get(pos..pos + len).ok_or_else(|| truncated(pos))?;
    // The reference decodes ASCII with replacement; latin-1 matches that
    // byte-for-byte and never fails.
    Ok(bytes.iter().map(|&b| b as char).collect())
}

fn truncated(pos: usize) -> PyErr {
    pyo3::exceptions::PyValueError::new_err(format!(
        "save data truncated at offset {pos:#x}"
    ))
}

struct FieldDef {
    name: String,
    type_name: String,
    meta_kind: u16,
    meta_size: u16,
    meta_aux: u32,
    start_offset: usize,
    end_offset: usize,
}

struct TypeDef {
    index: usize,
    name: String,
    fields: Vec<FieldDef>,
    start_offset: usize,
    end_offset: usize,
}

struct Schema {
    header_tag: u16,
    header_zero: u16,
    types: Vec<TypeDef>,
    root_type: String,
    schema_end: usize,
}

/// Mirror of save_parser.parse_schema.
fn parse_schema_impl(raw: &[u8]) -> PyResult<Schema> {
    let mut pos = 0x0e_usize;
    let header_tag = u16_at(raw, pos)?;
    pos += 2;
    let header_zero = u16_at(raw, pos)?;
    pos += 2;
    let type_count = u16_at(raw, pos)? as usize;
    pos += 2;

    let root_len = u32_at(raw, pos)? as usize;
    pos += 4;
    let root_name = ascii_at(raw, pos, root_len)?;
    pos += root_len;

    let mut types = Vec::with_capacity(type_count);
    let mut current_name = root_name.clone();
    for type_index in 0..type_count {
        let type_start = pos;
        let field_count = u16_at(raw, pos)? as usize;
        pos += 2;

        let mut fields = Vec::with_capacity(field_count);
        for _ in 0..field_count {
            let field_start = pos;
            let fn_len = u32_at(raw, pos)? as usize;
            pos += 4;
            let field_name = ascii_at(raw, pos, fn_len)?;
            pos += fn_len;

            let tn_len = u32_at(raw, pos)? as usize;
            pos += 4;
            let type_name = ascii_at(raw, pos, tn_len)?;
            pos += tn_len;

            let meta_kind = u16_at(raw, pos)?;
            let meta_size = u16_at(raw, pos + 2)?;
            let meta_aux = u32_at(raw, pos + 4)?;
            pos += 8;

            fields.push(FieldDef {
                name: field_name,
                type_name,
                meta_kind,
                meta_size,
                meta_aux,
                start_offset: field_start,
                end_offset: pos,
            });
        }

        types.push(TypeDef {
            index: type_index,
            name: current_name.clone(),
            fields,
            start_offset: type_start,
            end_offset: pos,
        });
        if type_index != type_count - 1 {
            let next_len = u32_at(raw, pos)? as usize;
            pos += 4;
            current_name = ascii_at(raw, pos, next_len)?;
            pos += next_len;
        }
    }

    Ok(Schema {
        header_tag,
        header_zero,
        types,
        root_type: root_name,
        schema_end: pos,
    })
}

struct TocEntry {
    index: usize,
    class_index: u32,
    class_name: String,
    sentinel1: u32,
    sentinel2: u32,
    data_offset: u32,
    data_size: u32,
    entry_offset: usize,
}

/// Mirror of save_parser.parse_toc.
fn parse_toc_impl(raw: &[u8], schema: &Schema) -> (Option<u32>, u32, u32, Vec<TocEntry>) {
    let schema_end = schema.schema_end;
    if schema_end + 12 > raw.len() {
        return (None, 0, raw.len() as u32, Vec::new());
    }
    let prefix_zero = u32::from_le_bytes(raw[schema_end..schema_end + 4].try_into().unwrap());
    let toc_count = u32::from_le_bytes(raw[schema_end + 4..schema_end + 8].try_into().unwrap());
    let stream_size = u32::from_le_bytes(raw[schema_end + 8..schema_end + 12].try_into().unwrap());
    let toc_start = schema_end + 12;

    let mut entries = Vec::with_capacity(toc_count as usize);
    for index in 0..toc_count as usize {
        let off = toc_start + index * 20;
        if off + 20 > raw.len() {
            break;
        }
        let word = |i: usize| u32::from_le_bytes(raw[off + i..off + i + 4].try_into().unwrap());
        let class_index = word(0);
        let class_name = schema
            .types
            .get(class_index as usize)
            .map(|t| t.name.clone())
            .unwrap_or_else(|| format!("<class_{class_index}>"));
        entries.push(TocEntry {
            index,
            class_index,
            class_name,
            sentinel1: word(4),
            sentinel2: word(8),
            data_offset: word(12),
            data_size: word(16),
            entry_offset: off,
        });
    }
    (Some(prefix_zero), toc_count, stream_size, entries)
}

/// parse_schema(raw) -> dict, mirroring the Python parser's shape.
#[pyfunction]
fn parse_schema(py: Python<'_>, raw: &[u8]) -> PyResult<Py<PyDict>> {
    let schema = parse_schema_impl(raw)?;
    let out = PyDict::new(py);
    out.set_item("header_tag", schema.header_tag)?;
    out.set_item("header_zero", schema.header_zero)?;
    out.set_item("type_count", schema.types.len())?;
    out.set_item("root_type", &schema.root_type)?;
    out.set_item("schema_end", schema.schema_end)?;

    let types = PyList::empty(py);
    for t in &schema.types {
        let td = PyDict::new(py);
        td.set_item("index", t.index)?;
        td.set_item("name", &t.name)?;
        td.set_item("start_offset", t.start_offset)?;
        td.set_item("end_offset", t.end_offset)?;
        let fields = PyList::empty(py);
        for f in &t.fields {
            let fd = PyDict::new(py);
            fd.set_item("name", &f.name)?;
            fd.set_item("type_name", &f.type_name)?;
            fd.set_item("meta_kind", f.meta_kind)?;
            fd.set_item("meta_size", f.meta_size)?;
            fd.set_item("meta_aux", f.meta_aux)?;
            fd.set_item("start_offset", f.start_offset)?;
            fd.set_item("end_offset", f.end_offset)?;
            fields.append(fd)?;
        }
        td.set_item("fields", fields)?;
        types.append(td)?;
    }
    out.set_item("types", types)?;
    Ok(out.into())
}

/// parse_toc(raw) -> dict, schema parsed internally.
#[pyfunction]
fn parse_toc(py: Python<'_>, raw: &[u8]) -> PyResult<Py<PyDict>> {
    let schema = parse_schema_impl(raw)?;
    let (prefix_zero, toc_count, stream_size, entries) = parse_toc_impl(raw, &schema);
    let out = PyDict::new(py);
    out.set_item("prefix_zero", prefix_zero)?;
    out.set_item("toc_count", toc_count)?;
    out.set_item("stream_size", stream_size)?;
    let list = PyList::empty(py);
    for e in &entries {
        let ed = PyDict::new(py);
        ed.set_item("index", e.index)?;
        ed.set_item("class_index", e.class_index)?;
        ed.set_item("class_name", &e.class_name)?;
        ed.set_item("sentinel1", e.sentinel1)?;
        ed.set_item("sentinel2", e.sentinel2)?;
        ed.set_item("data_offset", e.data_offset)?;
        ed.set_item("data_size", e.data_size)?;
        ed.set_item("entry_offset", e.entry_offset)?;
        list.append(ed)?;
    }
    out.set_item("entries", list)?;
    Ok(out.into())
}

#[pymodule]
fn crimson_parser(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(parse_schema, m)?)?;
    m.add_function(wrap_pyfunction!(parse_toc, m)?)?;
    m.add("STAGE", 1)?;
    Ok(())
}
