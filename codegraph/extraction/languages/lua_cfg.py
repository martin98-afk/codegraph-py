"""
Lua language extraction configuration.
"""
from tree_sitter import Node as TSNode
from .base import LanguageConfig, ImportInfo


class LuaConfig(LanguageConfig):
    """Lua extraction configuration."""

    language_id = 'lua'

    function_types = ['function_declaration']
    class_types = []
    method_types = ['function_declaration']  # Methods using self syntax
    interface_types = []
    struct_types = []
    enum_types = []
    type_alias_types = []
    import_types = ['require_statement']
    call_types = ['function_call']
    variable_types = ['variable_declaration', 'assignment_statement']
    field_types = ['field']
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
        if node.type == 'require_statement':
            text = source[node.start_byte:node.end_byte].decode('utf-8', errors='replace').strip()
            # require('module') or require "module"
            arg = node.child_by_field_name('module') or node.child(1)
            if arg:
                module_name = source[arg.start_byte:arg.end_byte].decode('utf-8', errors='replace')
                module_name = module_name.strip('\'"')
                return ImportInfo(module_name=module_name, signature=text)
        return None

    def is_exported(self, node: TSNode, source: bytes) -> bool:
        # Lua: functions in module scope, not starting with _
        name_node = node.child_by_field_name('name')
        if name_node:
            try:
                t = name_node.text
                if isinstance(t, bytes):
                    name = t.decode('utf-8', errors='replace')
                else:
                    name = str(t)
                return not name.startswith('_')
            except Exception:
                pass
        return True

    def classify_class_node(self, node: TSNode) -> str:
        # Lua uses metatables, treat function_declaration returning a table as class
        return 'class'
