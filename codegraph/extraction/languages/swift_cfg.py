"""
Swift language extraction configuration.
"""
from tree_sitter import Node as TSNode
from .base import LanguageConfig, ImportInfo


class SwiftConfig(LanguageConfig):
    """Swift extraction configuration."""

    language_id = 'swift'

    function_types = ['function_declaration']
    class_types = ['class_declaration']
    method_types = ['function_declaration', 'init_declaration', 'deinit_declaration']

    def get_name(self, node: TSNode, source: bytes) -> str | None:
        """Swift deinit has no name field; return 'deinit' explicitly."""
        if node.type == 'deinit_declaration':
            return 'deinit'
        return None
    interface_types = ['protocol_declaration']
    struct_types = ['struct_declaration']
    enum_types = ['enum_declaration']
    type_alias_types = ['typealias_declaration']
    import_types = ['import_declaration']
    call_types = ['call_expression']
    variable_types = ['variable_declaration']
    field_types = ['property_declaration']
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
        if node.type == 'import_declaration':
            text = source[node.start_byte:node.end_byte].decode('utf-8', errors='replace').strip()
            # Get the module path (all children after 'import' keyword)
            parts = []
            for child in node.named_children:
                t = source[child.start_byte:child.end_byte].decode('utf-8', errors='replace')
                parts.append(t)
            module_name = '.'.join(parts) if parts else ''
            return ImportInfo(module_name=module_name, signature=text)
        return None

    def is_exported(self, node: TSNode, source: bytes) -> bool:
        # Check for 'public' or 'open' modifiers
        for child in node.children:
            if child.type in ('public', 'open'):
                return True
        return False

    def is_static(self, node: TSNode) -> bool:
        for child in node.children:
            if child.type == 'static':
                return True
        return False
