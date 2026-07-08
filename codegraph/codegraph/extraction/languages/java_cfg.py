"""
Java language extraction configuration.
"""
from tree_sitter import Node as TSNode
from .base import LanguageConfig, ImportInfo


class JavaConfig(LanguageConfig):
    """Java extraction configuration."""

    language_id = 'java'

    function_types = []
    class_types = ['class_declaration']
    method_types = ['method_declaration']
    interface_types = ['interface_declaration']
    struct_types = []
    enum_types = ['enum_declaration']
    type_alias_types = []
    import_types = ['import_declaration']
    call_types = ['method_invocation']
    variable_types = ['variable_declaration', 'local_variable_declaration']
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
            type_text = source[return_type.start_byte:return_type.end_byte].decode('utf-8', errors='replace')
            sig = type_text + ' ' + sig
        return sig

    def extract_import(self, node: TSNode, source: bytes) -> ImportInfo | None:
        if node.type == 'import_declaration':
            text = source[node.start_byte:node.end_byte].decode('utf-8', errors='replace').strip()
            name = text.replace('import ', '').replace(';', '').strip()
            return ImportInfo(module_name=name, signature=text) if name else None
        return None

    def get_visibility(self, node: TSNode) -> str | None:
        # Walk previous siblings looking for modifier keywords
        prev = node.prev_sibling
        while prev is not None:
            if prev.type == 'public':
                return 'public'
            if prev.type == 'private':
                return 'private'
            if prev.type == 'protected':
                return 'protected'
            if prev.is_named:
                break
            prev = prev.prev_sibling
        return None
