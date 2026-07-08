"""
C++ language extraction configuration.
"""
from tree_sitter import Node as TSNode
from .base import LanguageConfig, ImportInfo


class CPPConfig(LanguageConfig):
    """C++ extraction configuration."""

    language_id = 'cpp'

    function_types = ['function_definition']
    class_types = ['class_specifier']
    method_types = ['function_definition']  # Functions inside class bodies
    interface_types = []
    struct_types = ['struct_specifier']
    enum_types = ['enum_specifier']
    type_alias_types = ['type_definition', 'alias_declaration']
    import_types = ['preproc_include', 'include_directive', 'using_declaration']
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
        elif node.type == 'using_declaration':
            text = source[node.start_byte:node.end_byte].decode('utf-8', errors='replace').strip()
            # Extract namespace from 'using namespace X;'
            args = node.child_by_field_name('argument')
            if args:
                module_name = source[args.start_byte:args.end_byte].decode('utf-8', errors='replace')
                return ImportInfo(module_name=module_name, signature=text)
        return None

    def is_exported(self, node: TSNode, source: bytes) -> bool:
        return True  # C++ functions at file scope are exported
