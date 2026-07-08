"""
Kotlin language extraction configuration.
"""
from tree_sitter import Node as TSNode
from .base import LanguageConfig, ImportInfo


class KotlinConfig(LanguageConfig):
    """Kotlin extraction configuration."""

    language_id = 'kotlin'

    function_types = ['function_declaration']
    class_types = ['class_declaration']
    method_types = ['function_declaration']  # Functions inside class bodies
    interface_types = ['interface_declaration']
    struct_types = []
    enum_types = ['enum_entry']
    type_alias_types = ['type_alias']
    import_types = ['import_header']
    call_types = ['call_expression']
    variable_types = ['property_declaration', 'variable_declaration']
    field_types = ['property_declaration']
    property_types = []

    name_field = 'name'
    body_field = 'body'
    params_field = 'parameters'
    return_field = 'type'

    def get_signature(self, node: TSNode, source: bytes) -> str | None:
        params = node.child_by_field_name('parameters')
        if not params:
            return None
        sig = source[params.start_byte:params.end_byte].decode('utf-8', errors='replace')
        return_type = node.child_by_field_name('type')
        if return_type:
            ret = source[return_type.start_byte:return_type.end_byte].decode('utf-8', errors='replace')
            sig += f': {ret}'
        return sig

    def extract_import(self, node: TSNode, source: bytes) -> ImportInfo | None:
        if node.type == 'import_header':
            text = source[node.start_byte:node.end_byte].decode('utf-8', errors='replace').strip()
            path_node = node.child_by_field_name('path')
            if path_node:
                module_name = source[path_node.start_byte:path_node.end_byte].decode('utf-8', errors='replace')
                return ImportInfo(module_name=module_name, signature=text)
        return None

    def is_exported(self, node: TSNode, source: bytes) -> bool:
        return True  # Kotlin functions are public by default
