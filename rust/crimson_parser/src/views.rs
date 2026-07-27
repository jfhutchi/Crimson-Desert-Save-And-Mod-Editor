//! Stage 3: lazy Python views over the native tree, with per-block lazy
//! decode.
//!
//! parse() decodes nothing but the schema, TOC and per-block headers. A
//! block's field tree materializes on the first touch of `.fields` and is
//! cached; blocks the application never inspects never cost more than their
//! header. Steady-state memory therefore tracks what the app actually uses.

use std::sync::{Arc, OnceLock};

use pyo3::prelude::*;
use pyo3::types::PyBytes;

use crate::decode::{decode_block_body, BlockHeader, Ctx, Node};
use crate::{Schema, TocEntry};

pub struct BlockBody {
    pub fields: Vec<Node>,
    pub undecoded: Vec<(usize, usize)>,
}

pub struct Parsed {
    pub raw: Vec<u8>,
    pub schema: Schema,
    pub entries: Vec<TocEntry>,
    /// One per TOC entry; None where Python's decoder skips the entry.
    pub headers: Vec<Option<BlockHeader>>,
    /// Lazily decoded bodies, same indexing as `entries`.
    pub cells: Vec<OnceLock<Arc<BlockBody>>>,
}

impl Parsed {
    pub fn ctx(&self) -> Ctx<'_> {
        Ctx {
            raw: &self.raw,
            schema: &self.schema,
        }
    }

    fn body(&self, index: usize) -> Arc<BlockBody> {
        self.cells[index]
            .get_or_init(|| {
                let header = self.headers[index]
                    .as_ref()
                    .expect("bodies are only requested for decodable entries");
                let (fields, undecoded) =
                    decode_block_body(&self.raw, &self.schema, &self.entries[index], header);
                Arc::new(BlockBody { fields, undecoded })
            })
            .clone()
    }
}

#[pyclass(name = "NativeParse")]
pub struct NativeParse {
    pub inner: Arc<Parsed>,
}

#[pymethods]
impl NativeParse {
    /// The block list, mirroring result["objects"]. Bodies stay undecoded
    /// until a block's fields are first touched.
    #[getter]
    fn objects(&self) -> Vec<BlockView> {
        self.inner
            .headers
            .iter()
            .enumerate()
            .filter_map(|(index, header)| {
                header.as_ref().map(|_| BlockView {
                    inner: self.inner.clone(),
                    index,
                })
            })
            .collect()
    }

    /// How many block bodies have actually been decoded so far.
    #[getter]
    fn decoded_blocks(&self) -> usize {
        self.inner
            .cells
            .iter()
            .filter(|cell| cell.get().is_some())
            .count()
    }

    fn __len__(&self) -> usize {
        self.inner.headers.iter().flatten().count()
    }
}

#[pyclass(name = "ObjectBlock")]
pub struct BlockView {
    inner: Arc<Parsed>,
    index: usize,
}

impl BlockView {
    fn entry(&self) -> &TocEntry {
        &self.inner.entries[self.index]
    }
    fn header(&self) -> &BlockHeader {
        self.inner.headers[self.index].as_ref().unwrap()
    }
}

#[pymethods]
impl BlockView {
    #[getter]
    fn entry_index(&self) -> usize {
        self.entry().index
    }
    #[getter]
    fn class_index(&self) -> u32 {
        self.entry().class_index
    }
    #[getter]
    fn class_name(&self) -> String {
        let index = self.entry().class_index as usize;
        self.inner
            .schema
            .types
            .get(index)
            .map(|t| t.name.clone())
            .unwrap_or_else(|| format!("<class_{index}>"))
    }
    #[getter]
    fn data_offset(&self) -> usize {
        self.entry().data_offset as usize
    }
    #[getter]
    fn data_size(&self) -> usize {
        self.entry().data_size as usize
    }
    #[getter]
    fn mask_byte_count(&self) -> usize {
        self.header().mask_byte_count
    }
    #[getter]
    fn header_mask_bytes<'py>(&self, py: Python<'py>) -> Bound<'py, PyBytes> {
        PyBytes::new(py, self.inner.ctx().mask_bytes(self.header().mask_span))
    }
    #[getter]
    fn reserved_u32(&self) -> u32 {
        self.header().reserved_u32
    }
    #[getter]
    fn fields(&self) -> Vec<NodeView> {
        let body = self.inner.body(self.index);
        body.fields
            .iter()
            .map(|node| NodeView {
                inner: self.inner.clone(),
                body: body.clone(),
                node: NodePtr(node as *const Node),
            })
            .collect()
    }
    #[getter]
    fn undecoded_ranges(&self) -> Vec<(usize, usize)> {
        self.inner.body(self.index).undecoded.clone()
    }
}

/// A raw pointer into the Arc-owned immutable tree. Safe to send between
/// threads: the pointee is never mutated after construction and both owning
/// Arcs travel with the view.
struct NodePtr(*const Node);
unsafe impl Send for NodePtr {}
unsafe impl Sync for NodePtr {}

#[pyclass(name = "GenericFieldValue")]
pub struct NodeView {
    inner: Arc<Parsed>,
    body: Arc<BlockBody>,
    node: NodePtr,
}

impl NodeView {
    fn get(&self) -> &Node {
        // Safety: `body` owns the subtree the pointer targets and the tree
        // is never mutated after construction.
        unsafe { &*self.node.0 }
    }

    fn wrap(&self, node: &Node) -> NodeView {
        NodeView {
            inner: self.inner.clone(),
            body: self.body.clone(),
            node: NodePtr(node as *const Node),
        }
    }
}

#[pymethods]
impl NodeView {
    #[getter]
    fn field_index(&self) -> usize {
        self.get().field_index
    }
    #[getter]
    fn name(&self) -> String {
        self.inner.ctx().node_name(self.get())
    }
    #[getter]
    fn type_name(&self) -> String {
        self.inner.ctx().node_type_name(self.get())
    }
    #[getter]
    fn meta_kind(&self) -> u16 {
        self.get().meta_kind
    }
    #[getter]
    fn meta_size(&self) -> u16 {
        self.get().meta_size
    }
    #[getter]
    fn meta_aux(&self) -> u32 {
        self.get().meta_aux
    }
    #[getter]
    fn present(&self) -> bool {
        self.get().present
    }
    #[getter]
    fn decode_kind(&self) -> &'static str {
        self.get().decode_kind
    }
    #[getter]
    fn start_offset(&self) -> usize {
        self.get().start_offset
    }
    #[getter]
    fn end_offset(&self) -> usize {
        self.get().end_offset
    }
    #[getter]
    fn value_repr(&self) -> String {
        self.inner.ctx().value_repr(self.get())
    }
    #[getter]
    fn edit_format(&self) -> &'static str {
        self.get().edit_format
    }
    #[getter]
    fn editable(&self) -> bool {
        self.get().editable
    }
    #[getter]
    fn note(&self) -> String {
        self.inner.ctx().note_repr(self.get())
    }
    #[getter]
    fn child_prefix_u16(&self) -> u16 {
        self.get().child_prefix_u16
    }
    #[getter]
    fn child_prefix_u8(&self) -> u8 {
        self.get().child_prefix_u8
    }
    #[getter]
    fn child_mask_byte_count(&self) -> usize {
        self.get().child_mask_byte_count
    }
    #[getter]
    fn child_mask_bytes<'py>(&self, py: Python<'py>) -> Bound<'py, PyBytes> {
        let n = self.get();
        if n.child_mask_byte_count > 0 {
            PyBytes::new(py, self.inner.ctx().mask_bytes(n.child_mask_span))
        } else {
            PyBytes::new(py, b"")
        }
    }
    #[getter]
    fn child_type_index(&self) -> i64 {
        self.get().child_type_index
    }
    #[getter]
    fn child_type_name(&self) -> String {
        let n = self.get();
        if n.child_type_index < 0 {
            return String::new();
        }
        self.inner.ctx().child_type_name(n)
    }
    #[getter]
    fn child_reserved_u8(&self) -> u8 {
        self.get().child_reserved_u8
    }
    #[getter]
    fn child_sentinel1_u32(&self) -> u32 {
        self.get().child_sentinel1_u32
    }
    #[getter]
    fn child_sentinel2_u32(&self) -> u32 {
        self.get().child_sentinel2_u32
    }
    #[getter]
    fn child_payload_offset(&self) -> usize {
        self.get().child_payload_offset
    }
    #[getter]
    fn child_reserved_u32(&self) -> u32 {
        self.get().child_reserved_u32
    }
    #[getter]
    fn child_size_u32(&self) -> u32 {
        self.get().child_size_u32
    }
    #[getter]
    fn child_fields(&self) -> Option<Vec<NodeView>> {
        self.get()
            .child_fields
            .as_ref()
            .map(|children| children.iter().map(|node| self.wrap(node)).collect())
    }
    #[getter]
    fn child_undecoded_ranges(&self) -> Option<Vec<(usize, usize)>> {
        self.get().child_undecoded.clone()
    }
    #[getter]
    fn list_prefix_u8(&self) -> u8 {
        self.get().list_prefix_u8
    }
    #[getter]
    fn list_count(&self) -> u32 {
        self.get().list_count
    }
    #[getter]
    fn list_reserved1_u32(&self) -> u32 {
        self.get().list_reserved1_u32
    }
    #[getter]
    fn list_reserved2_u32(&self) -> u32 {
        self.get().list_reserved2_u32
    }
    #[getter]
    fn list_reserved3_u32(&self) -> u32 {
        self.get().list_reserved3_u32
    }
    #[getter]
    fn list_reserved4_u16(&self) -> u16 {
        self.get().list_reserved4_u16
    }
    #[getter]
    fn list_reserved4_u32(&self) -> u32 {
        self.get().list_reserved4_u32
    }
    #[getter]
    fn list_header_size(&self) -> usize {
        self.get().list_header_size
    }
    #[getter]
    fn list_elements(&self) -> Option<Vec<NodeView>> {
        self.get()
            .list_elements
            .as_ref()
            .map(|elements| elements.iter().map(|node| self.wrap(node)).collect())
    }
}
