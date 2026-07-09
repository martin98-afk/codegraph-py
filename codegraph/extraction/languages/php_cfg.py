"""
PHP language extraction configuration.
"""
from tree_sitter import Node as TSNode
from .base import LanguageConfig, ImportInfo


class PHPConfig(LanguageConfig):
    """PHP extraction configuration."""

    language_id = 'php'

    function_types = ['function_definition']
    class_types = ['class_declaration']
    method_types = ['method_declaration']
    interface_types = ['interface_declaration']
    struct_types = ['trait_declaration']
    enum_types = ['enum_declaration']
    type_alias_types = []
    import_types = ['use_declaration', 'namespace_definition']
    call_types = ['function_call_expression', 'member_call_expression',
                  'scoped_call_expression']
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
            # method_declaration uses 'formal_parameters' instead of 'parameters'
            params = node.child_by_field_name('formal_parameters')
        if not params:
            return None
        sig = source[params.start_byte:params.end_byte].decode('utf-8', errors='replace')
        return_type = node.child_by_field_name('return_type')
        if return_type:
            sig += ': ' + source[return_type.start_byte:return_type.end_byte].decode('utf-8', errors='replace')
        return sig

    def get_visibility(self, node: TSNode) -> str | None:
        for child in node.children:
            if child.type == 'visibility_modifier':
                for mod in child.named_children:
                    if mod.type in ('public', 'private', 'protected'):
                        return mod.type
        return None

    def is_exported(self, node: TSNode, source: bytes) -> bool:
        # PHP has no file-level export concept; all top-level symbols are accessible
        return True

    def is_static(self, node: TSNode) -> bool:
        for child in node.children:
            if child.type == 'static_modifier' or child.type == 'static':
                return True
        return False

    def extract_import(self, node: TSNode, source: bytes) -> ImportInfo | None:
        if node.type == 'use_declaration':
            text = source[node.start_byte:node.end_byte].decode('utf-8', errors='replace').strip()
            # Extract the fully qualified class/namespace name
            for child in node.named_children:
                if child.type == 'namespace_name':
                    module_name = source[child.start_byte:child.end_byte].decode('utf-8', errors='replace')
                    return ImportInfo(module_name=module_name, signature=text)
            return ImportInfo(module_name='', signature=text)
        elif node.type == 'namespace_definition':
            text = source[node.start_byte:node.end_byte].decode('utf-8', errors='replace').strip()
            name_node = node.child_by_field_name('name')
            if name_node:
                module_name = source[name_node.start_byte:name_node.end_byte].decode('utf-8', errors='replace')
                return ImportInfo(module_name=module_name, signature=text)
            return ImportInfo(module_name='', signature=text)
        return None

    def extract_modifiers(self, node: TSNode) -> list[str]:
        mods = []
        for child in node.children:
            if child.type == 'visibility_modifier':
                for mod in child.named_children:
                    mods.append(mod.type)
            if child.type == 'static_modifier':
                mods.append('static')
            if child.type == 'abstract_modifier':
                mods.append('abstract')
            if child.type == 'final_modifier':
                mods.append('final')
            if child.type == 'readonly_modifier':
                mods.append('readonly')
        return mods
