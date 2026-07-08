"""
Dart language extraction configuration.
"""
from tree_sitter import Node as TSNode
from .base import LanguageConfig, ImportInfo


class DartConfig(LanguageConfig):
    """Dart extraction configuration."""

    language_id = 'dart'

    function_types = ['function_declaration']
    class_types = ['class_declaration']
    method_types = ['method_declaration']
    interface_types = ['interface_type']  # Dart uses abstract classes + implements
    struct_types = []
    enum_types = ['enum_declaration']
    type_alias_types = ['type_alias']
    import_types = ['import_directive', 'export_directive']
    call_types = ['function_expression']
    variable_types = ['variable_declaration']
    field_types = ['field_declaration']
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
            ret = source[return_type.start_byte:return_type.end_byte].decode('utf-8', errors='replace')
            sig += f' -> {ret}'
        return sig

    def extract_import(self, node: TSNode, source: bytes) -> ImportInfo | None:
        if node.type in ('import_directive', 'export_directive'):
            text = source[node.start_byte:node.end_byte].decode('utf-8', errors='replace').strip()
            uri_node = node.child_by_field_name('uri')
            if uri_node:
                module_name = source[uri_node.start_byte:uri_node.end_byte].decode('utf-8', errors='replace')
                module_name = module_name.strip('\'"')
                return ImportInfo(module_name=module_name, signature=text)
        return None

    def is_exported(self, node: TSNode, source: bytes) -> bool:
        return True  # Exported via library/part directives
