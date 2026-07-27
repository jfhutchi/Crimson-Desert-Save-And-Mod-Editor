//! Stage 3: lazy Python views over the native tree.
//!
//! The tree lives in Rust; Python receives featherweight view objects that
//! materialize attributes on access - including every derived string, which
//! the slim nodes no longer store. A view holds an Arc to the whole parse
//! plus a pointer into it; the tree is immutable and the Arc outlives every
//! view.

use std::sync::Arc;

use pyo3::prelude::*;
use pyo3::types::PyBytes;

use crate::decode::{Block, Ctx, Node};
use crate::Schema;

pub struct Parsed {
    pub raw: Vec<u8>,
    pub schema: Schema,
    pub blocks: Vec<Block>,
}

impl Parsed {
    pub fn ctx(&self) -> Ctx<'_> {
        Ctx {
            raw: &self.raw,
            schema: &self.schema,
        }
    }
}

#[pyclass(unsendable, name = "NativeParse")]
pub struct NativeParse {
    pub inner: Arc<Parsed>,
}

#[pymethods]
impl NativeParse {
    /// The block list, mirroring result["objects"].
    #[getter]
    fn objects(&self) -> Vec<BlockView> {
        (0..self.inner.blocks.len())
            .map(|index| BlockView {
                inner: self.inner.clone(),
                index,
            })
            .collect()
    }

    fn __len__(&self) -> usize {
        self.inner.blocks.len()
    }
}

#[pyclass(unsendable, name = "ObjectBlock")]
pub struct BlockView {
    inner: Arc<Parsed>,
    index: usize,
}

impl BlockView {
    fn block(&self) -> &Block {
        &self.inner.blocks[self.index]
    }
}

#[pymethods]
impl BlockView {
    #[getter]
    fn entry_index(&self) -> usize {
        self.block().entry_index
    }
    #[getter]
    fn class_index(&self) -> u32 {
        self.block().class_index
    }
    #[getter]
    fn class_name(&self) -> String {
        let index = self.block().class_index as usize;
        self.inner
            .schema
            .types
            .get(index)
            .map(|t| t.name.clone())
            .unwrap_or_else(|| format!("<class_{index}>"))
    }
    #[getter]
    fn data_offset(&self) -> usize {
        self.block().data_offset
    }
    #[getter]
    fn data_size(&self) -> usize {
        self.block().data_size
    }
    #[getter]
    fn mask_byte_count(&self) -> usize {
        self.block().mask_byte_count
    }
    #[getter]
    fn header_mask_bytes<'py>(&self, py: Python<'py>) -> Bound<'py, PyBytes> {
        PyBytes::new(py, self.inner.ctx().mask_bytes(self.block().mask_span))
    }
    #[getter]
    fn reserved_u32(&self) -> u32 {
        self.block().reserved_u32
    }
    #[getter]
    fn fields(&self) -> Vec<NodeView> {
        self.block()
            .fields
            .iter()
            .map(|node| NodeView {
                inner: self.inner.clone(),
                node: node as *const Node,
            })
            .collect()
    }
    #[getter]
    fn undecoded_ranges(&self) -> Vec<(usize, usize)> {
        self.block().undecoded.clone()
    }
}

#[pyclass(unsendable, name = "GenericFieldValue")]
pub struct NodeView {
    inner: Arc<Parsed>,
    node: *const Node,
}

impl NodeView {
    fn get(&self) -> &Node {
        // Safety: `inner` owns the tree the pointer targets, the tree is
        // never mutated after construction, and views are unsendable.
        unsafe { &*self.node }
    }

    fn wrap(&self, node: &Node) -> NodeView {
        NodeView {
            inner: self.inner.clone(),
            node: node as *const Node,
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
