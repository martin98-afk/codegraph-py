"""
C language extraction configuration.
"""
from tree_sitter import Node as TSNode
from .base import LanguageConfig, ImportInfo


class CConfig(LanguageConfig):
    """C extraction configuration."""

    language_id = 'c'

    function_types = ['function_definition']
    class_types = []
    method_types = []
    interface_types = []
    struct_types = ['struct_specifier']
    enum_types = ['enum_specifier']
    type_alias_types = ['type_definition']
    import_types = ['preproc_include', 'include_directive']
    call_types = ['call_expression']
    variable_types = ['declaration']
    field_types = ['field_declaration']
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
            sig = f'{ret} {sig}'
        return sig

    def extract_import(self, node: TSNode, source: bytes) -> ImportInfo | None:
        if node.type in ('preproc_include', 'include_directive'):
            text = source[node.start_byte:node.end_byte].decode('utf-8', errors='replace').strip()
            path_node = node.child_by_field_name('path')
            if path_node:
                module_name = source[path_node.start_byte:path_node.end_byte].decode('utf-8', errors='replace')
                module_name = module_name.strip('<>"')
                return ImportInfo(module_name=module_name, signature=text)
        return None

    def is_exported(self, node: TSNode, source: bytes) -> bool:
        return True  # All C functions at file scope are effectively exported
