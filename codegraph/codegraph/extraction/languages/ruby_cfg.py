"""
Ruby language extraction configuration.
"""
from tree_sitter import Node as TSNode
from .base import LanguageConfig, ImportInfo


class RubyConfig(LanguageConfig):
    """Ruby extraction configuration."""

    language_id = 'ruby'

    function_types = ['method']
    class_types = ['class']
    method_types = ['method']  # Methods inside class bodies
    interface_types = []
    struct_types = []
    enum_types = []
    type_alias_types = []
    import_types = ['require', 'require_relative']
    call_types = ['call', 'method_call']
    variable_types = ['assignment']
    field_types = []
    property_types = []

    name_field = 'name'
    body_field = 'body'
    params_field = 'parameters'
    return_field = None

    def get_signature(self, node: TSNode, source: bytes) -> str | None:
        params = node.child_by_field_name('parameters')
        if not params:
            return None
        return source[params.start_byte:params.end_byte].decode('utf-8', errors='replace')

    def extract_import(self, node: TSNode, source: bytes) -> ImportInfo | None:
        text = source[node.start_byte:node.end_byte].decode('utf-8', errors='replace').strip()
        if node.type in ('require', 'require_relative'):
            arg = node.child_by_field_name('path') or node.child(1)
            if arg:
                module_name = source[arg.start_byte:arg.end_byte].decode('utf-8', errors='replace')
                module_name = module_name.strip('\'"')
                return ImportInfo(module_name=module_name, signature=text)
        return None

    def is_exported(self, node: TSNode, source: bytes) -> bool:
        return not node.type.startswith('_')
