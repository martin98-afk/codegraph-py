"""
Rust language extraction configuration.
"""
from tree_sitter import Node as TSNode
from .base import LanguageConfig, ImportInfo


class RustConfig(LanguageConfig):
    """Rust extraction configuration."""

    language_id = 'rust'

    function_types = ['function_item']
    class_types = ['impl_item']
    method_types = ['function_item']  # Methods in impl blocks
    interface_types = ['trait_item']
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

    def get_name(self, node: TSNode, source: bytes) -> str | None:
        """Custom name extraction for Rust impl_item.

        impl_item has 'type' and 'trait' fields but no 'name' field.
        Use the type being implemented as the class name.
        """
        if node.type == 'impl_item':
            type_node = node.child_by_field_name('type')
            if type_node is not None:
                return source[type_node.start_byte:type_node.end_byte].decode('utf-8', errors='replace')
            return None
        return None

    def extract_implements(self, node: TSNode, source: bytes) -> list[str]:
        """Extract trait names from 'impl Trait for Type' blocks.

        For 'impl Greeter for Person', the class node is 'Person'
        and it implements 'Greeter'.
        """
        if node.type != 'impl_item':
            return []
        trait_node = node.child_by_field_name('trait')
        if trait_node is None:
            # Inherent impl - no trait being implemented
            return []
        # trait_node may be a generic_type or type_identifier
        if trait_node.type == 'generic_type':
            for sub in trait_node.named_children:
                if sub.type == 'type_identifier':
                    return [source[sub.start_byte:sub.end_byte].decode('utf-8', errors='replace')]
        return [source[trait_node.start_byte:trait_node.end_byte].decode('utf-8', errors='replace')]

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
