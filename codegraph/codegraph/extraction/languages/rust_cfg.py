"""
Rust language extraction configuration.
"""
from tree_sitter import Node as TSNode
from .base import LanguageConfig, ImportInfo


class RustConfig(LanguageConfig):
    """Rust extraction configuration."""

    language_id = 'rust'

    function_types = ['function_item']
    class_types = []
    method_types = ['function_item']  # Methods in impl blocks
    interface_types = []
    struct_types = ['struct_item']
    enum_types = ['enum_item']
    type_alias_types = ['type_item']
    import_types = ['use_declaration']
    call_types = ['call_expression']
    variable_types = ['let_declaration']
    field_types = []
    property_types = []

    name_field = 'name'
    body_field = 'body'
    params_field = 'parameters'
    return_field = 'return_type'

    def get_signature(self, node: TSNode, source: bytes) -> str | None:
        params = node.child_by_field_name('parameters')
        if not params:
            return None
        sig = source[params.start_byte:params.end_byte].decode('utf-8', errors='replace')
        return_type = node.child_by_field_name('return_type')
        if return_type:
            sig += ' -> ' + source[return_type.start_byte:return_type.end_byte].decode('utf-8', errors='replace')
        return sig

    def extract_import(self, node: TSNode, source: bytes) -> ImportInfo | None:
        if node.type == 'use_declaration':
            text = source[node.start_byte:node.end_byte].decode('utf-8', errors='replace').strip()
            # Extract the first path segment
            arg = node.child_by_field_name('argument')
            if arg:
                module_name = source[arg.start_byte:arg.end_byte].decode('utf-8', errors='replace')
                # Get the top-level crate/module name
                top_level = module_name.split('::')[0]
                return ImportInfo(module_name=top_level, signature=text)
        return None

    def is_exported(self, node: TSNode, source: bytes) -> bool:
        # Check for 'pub' keyword
        prev = node.prev_sibling
        while prev is not None:
            if prev.type == 'pub':
                return True
            if prev.is_named and prev.type != 'pub':
                break
            prev = prev.prev_sibling
        return False

    def classify_class_node(self, node: TSNode) -> str:
        # Rust uses `impl` blocks for implementations
        if node.type == 'impl_item':
            return 'class'  # Treat impl blocks as containers for methods
        return 'class'
