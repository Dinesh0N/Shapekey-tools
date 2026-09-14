bl_info = {
    "name": "Shape Key Value Transfer & Presets",
    "author": "Dinesh",
    "version": (1, 3, 0),
    "blender": (4, 2, 0),
    "location": "3D Viewport > Sidebar (N) > Tool > Shape Key Presets",
    "description": "Copy/paste shape keys across files with native preset management and old value restoration",
    "category": "Mesh",
}

import bpy
import json
import os
from bpy.props import StringProperty, BoolProperty, PointerProperty, EnumProperty


PRESET_SUBDIR = "mesh_shapekeys"


# -------------------------------------------------------------------
# Helper Utilities & Preset Directory
# -------------------------------------------------------------------

def get_preset_path():
    """Returns absolute path to the user's Blender presets directory."""
    return bpy.utils.user_resource('SCRIPTS', path=f"presets/{PRESET_SUBDIR}", create=True)


def get_shapekey_dict(obj):
    """Extracts non-basis shape key name:value mappings."""
    if not obj or obj.type != 'MESH' or not obj.data or not obj.data.shape_keys:
        return {}
    key_blocks = obj.data.shape_keys.key_blocks
    ref_key = obj.data.shape_keys.reference_key
    return {kb.name: float(kb.value) for kb in key_blocks if kb != ref_key}


def apply_shapekey_dict(obj, data_dict):
    """Applies values to keys that exist on target mesh."""
    if not obj or obj.type != 'MESH' or not obj.data or not obj.data.shape_keys:
        return 0

    key_blocks = obj.data.shape_keys.key_blocks
    count = 0
    for name, val in data_dict.items():
        if name in key_blocks:
            key_blocks[name].value = float(val)
            count += 1

    obj.data.update()
    return count


# -------------------------------------------------------------------
# Dynamic Enum for Presets Dropdown
# -------------------------------------------------------------------

def get_preset_items(self, context):
    """Dynamically reads .py / .json preset files from disk."""
    folder = get_preset_path()
    items = [("NONE", "Presets", "Available Presets")]
    if os.path.exists(folder):
        files = sorted([f for f in os.listdir(folder) if f.endswith(".json")])
        for idx, f in enumerate(files):
            name = os.path.splitext(f)[0]
            items.append((name, name, f"Apply preset {name}"))
    return items


def on_preset_selected(self, context):
    """Triggers when user selects a preset from the native dropdown."""
    preset_name = self.active_preset
    if preset_name == "NONE":
        return

    folder = get_preset_path()
    filepath = os.path.join(folder, f"{preset_name}.json")
    if not os.path.exists(filepath):
        return

    obj = context.active_object
    if not obj or obj.type != 'MESH' or not obj.data or not obj.data.shape_keys:
        return

    try:
        with open(filepath, 'r', encoding='utf-8') as fp:
            data = json.load(fp)

        # Snapshot old values before applying preset
        self.old_values_json = json.dumps(get_shapekey_dict(obj))
        self.has_backup = True

        apply_shapekey_dict(obj, data)
    except Exception as e:
        print(f"[ShapeKey Presets] Error reading preset: {e}")


# -------------------------------------------------------------------
# Property Group
# -------------------------------------------------------------------

class ShapeKeyTransferSettings(bpy.types.PropertyGroup):
    active_preset: EnumProperty(
        name="Presets",
        description="Select a shape key preset",
        items=get_preset_items,
        update=on_preset_selected,
    )
    old_values_json: StringProperty(name="Old Values JSON", default="{}")
    has_backup: BoolProperty(name="Has Backup", default=False)


# -------------------------------------------------------------------
# Operators
# -------------------------------------------------------------------

class MESH_OT_add_shapekey_preset(bpy.types.Operator):
    """Save current shape key values as a named disk preset"""
    bl_idname = "mesh.shapekey_preset_add"
    bl_label = "Add Shape Key Preset"
    bl_property = "preset_name"

    preset_name: StringProperty(
        name="Preset Name",
        description="Enter name for the preset",
        default="New_Preset"
    )

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH' or not obj.data or not obj.data.shape_keys:
            self.report({'WARNING'}, "Active object has no shape keys")
            return {'CANCELLED'}

        clean_name = bpy.path.clean_name(self.preset_name.strip())
        if not clean_name:
            self.report({'ERROR'}, "Invalid preset name")
            return {'CANCELLED'}

        data = get_shapekey_dict(obj)
        folder = get_preset_path()
        filepath = os.path.join(folder, f"{clean_name}.json")

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)

        self.report({'INFO'}, f"Saved preset '{clean_name}'")
        return {'FINISHED'}


class MESH_OT_remove_shapekey_preset(bpy.types.Operator):
    """Delete currently selected preset from disk"""
    bl_idname = "mesh.shapekey_preset_remove"
    bl_label = "Remove Preset"

    @classmethod
    def poll(cls, context):
        settings = getattr(context.window_manager, "shapekey_transfer_settings", None)
        return bool(settings and settings.active_preset != "NONE")

    def execute(self, context):
        settings = context.window_manager.shapekey_transfer_settings
        preset_name = settings.active_preset
        folder = get_preset_path()
        filepath = os.path.join(folder, f"{preset_name}.json")

        if os.path.exists(filepath):
            os.remove(filepath)
            settings.active_preset = "NONE"
            self.report({'INFO'}, f"Removed preset '{preset_name}'")
            return {'FINISHED'}

        self.report({'WARNING'}, "Preset file not found")
        return {'CANCELLED'}


class MESH_OT_copy_shapekey_values(bpy.types.Operator):
    """Copy active object's shape key values to system clipboard"""
    bl_idname = "mesh.copy_shapekey_values"
    bl_label = "Copy Values"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return bool(obj and obj.type == 'MESH' and obj.data and obj.data.shape_keys)

    def execute(self, context):
        obj = context.active_object
        data = get_shapekey_dict(obj)

        if not data:
            self.report({'WARNING'}, "No non-basis shape keys found on active mesh")
            return {'CANCELLED'}

        context.window_manager.clipboard = json.dumps(data, indent=2)
        self.report({'INFO'}, f"Copied {len(data)} shape key values to clipboard")
        return {'FINISHED'}


class MESH_OT_paste_shapekey_values(bpy.types.Operator):
    """Paste shape key values from clipboard and store prior values in backup buffer"""
    bl_idname = "mesh.paste_shapekey_values"
    bl_label = "Paste Values"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return bool(obj and obj.type == 'MESH' and obj.data and obj.data.shape_keys)

    def execute(self, context):
        obj = context.active_object
        clipboard = context.window_manager.clipboard.strip()

        if not clipboard:
            self.report({'ERROR'}, "Clipboard is empty")
            return {'CANCELLED'}

        try:
            target_data = json.loads(clipboard)
            if not isinstance(target_data, dict):
                raise ValueError
        except Exception:
            self.report({'ERROR'}, "Clipboard does not contain valid shape key JSON")
            return {'CANCELLED'}

        # Snapshot current values before pasting
        settings = context.window_manager.shapekey_transfer_settings
        settings.old_values_json = json.dumps(get_shapekey_dict(obj))
        settings.has_backup = True

        applied = apply_shapekey_dict(obj, target_data)
        self.report({'INFO'}, f"Pasted {applied} shape key values (Old values backed up)")
        return {'FINISHED'}


class MESH_OT_restore_old_shapekey_values(bpy.types.Operator):
    """Restore the previous values before last paste or preset load"""
    bl_idname = "mesh.restore_old_shapekey_values"
    bl_label = "Restore Old Values"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        settings = getattr(context.window_manager, "shapekey_transfer_settings", None)
        obj = context.active_object
        return bool(
            settings and settings.has_backup and
            obj and obj.type == 'MESH' and obj.data and obj.data.shape_keys
        )

    def execute(self, context):
        obj = context.active_object
        settings = context.window_manager.shapekey_transfer_settings

        try:
            backup_dict = json.loads(settings.old_values_json)
        except Exception:
            self.report({'ERROR'}, "Failed to read backup state")
            return {'CANCELLED'}

        applied = apply_shapekey_dict(obj, backup_dict)
        self.report({'INFO'}, f"Restored {applied} old shape key values")
        return {'FINISHED'}


# -------------------------------------------------------------------
# Panels
# -------------------------------------------------------------------

def draw_main_ui(layout, context):
    obj = context.active_object
    settings = context.window_manager.shapekey_transfer_settings

    # Default Blender-style preset header
    row = layout.row(align=True)
    row.prop(settings, "active_preset", text="")
    row.operator("mesh.shapekey_preset_add", text="", icon='ADD')
    row.operator("mesh.shapekey_preset_remove", text="", icon='REMOVE')

    layout.separator()

    if not obj:
        layout.label(text="Select an object", icon='INFO')
        return
    if obj.type != 'MESH':
        layout.label(text=f"'{obj.name}' is not a Mesh", icon='OBJECT_DATA')
        return
    if not obj.data or not obj.data.shape_keys:
        layout.label(text="No Shape Keys found", icon='SHAPEKEY_DATA')
        return

    # Cross-file clipboard buttons
    box = layout.box()
    box.label(text="Clipboard Transfer", icon='COPYDOWN')
    col = box.column(align=True)
    row_c = col.row(align=True)
    row_c.operator("mesh.copy_shapekey_values", icon='COPY_ID')
    row_c.operator("mesh.paste_shapekey_values", icon='PASTEDOWN')

    # Old Value Rollback
    layout.separator(factor=0.5)
    row_undo = layout.row(align=True)
    row_undo.enabled = settings.has_backup
    row_undo.operator("mesh.restore_old_shapekey_values", icon='BACK')


class VIEW3D_PT_shapekey_transfer(bpy.types.Panel):
    """Visible in 3D Viewport > Sidebar (N) > Tool"""
    bl_label = "Shape Key Presets"
    bl_idname = "VIEW3D_PT_shapekey_transfer"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Tool"

    def draw(self, context):
        draw_main_ui(self.layout, context)


class DATA_PT_shapekey_transfer_mesh(bpy.types.Panel):
    """Visible in Properties > Object Data Properties (Mesh Icon)"""
    bl_label = "Shape Key Presets & Transfer"
    bl_idname = "DATA_PT_shapekey_transfer_mesh"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "data"

    def draw(self, context):
        draw_main_ui(self.layout, context)


# -------------------------------------------------------------------
# Registration
# -------------------------------------------------------------------

classes = (
    ShapeKeyTransferSettings,
    MESH_OT_add_shapekey_preset,
    MESH_OT_remove_shapekey_preset,
    MESH_OT_copy_shapekey_values,
    MESH_OT_paste_shapekey_values,
    MESH_OT_restore_old_shapekey_values,
    VIEW3D_PT_shapekey_transfer,
    DATA_PT_shapekey_transfer_mesh,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.shapekey_transfer_settings = PointerProperty(type=ShapeKeyTransferSettings)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    if hasattr(bpy.types.WindowManager, "shapekey_transfer_settings"):
        del bpy.types.WindowManager.shapekey_transfer_settings


if __name__ == "__main__":
    register()
