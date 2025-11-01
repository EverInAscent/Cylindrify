bl_info = {
    "name": "Cylindrify",
    "author": "John & ChatGPT",
    "version": (1, 1, 0),
    "blender": (3, 3, 0),
    "location": "View3D > Sidebar (N) > Cylinder SVG",
    "description": "Import SVG, set cylinder dimensions (outer, inner, height), create subdivided cube, place SVG, and cylindrify by 360°. Optional apparent-width preservation.",
    "category": "3D View",
}

import bpy
from bpy.types import Operator, Panel, PropertyGroup
from bpy.props import StringProperty, EnumProperty, FloatProperty, PointerProperty, BoolProperty
from math import radians, pi, asin
from mathutils import Vector

# ---------------------------
# Helpers
# ---------------------------

def _to_meters(value: float, unit: str = "m") -> float:
    unit = (unit or "m").lower()
    if unit == "mm": return value / 1000.0
    if unit == "cm": return value / 100.0
    return value

def _ensure_subsurf(obj, name="CYLSVG_Subd", levels=2):
    mod = obj.modifiers.get(name)
    if not mod:
        mod = obj.modifiers.new(name, 'SUBSURF')
    mod.levels = int(max(1, levels))
    mod.render_levels = mod.levels
    mod.subdivision_type = 'SIMPLE'
    return mod

def _apply_modifiers(obj):
    if obj is None: return
    ctx = bpy.context
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    ctx.view_layer.objects.active = obj
    for m in list(obj.modifiers):
        try:
            bpy.ops.object.modifier_apply(modifier=m.name)
        except Exception:
            pass

def _world_bbox(obj):
    return [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]

def _world_bbox_min_max_z(obj):
    zs = [c.z for c in _world_bbox(obj)]
    return min(zs), max(zs)

def _world_bbox_center_xy(obj):
    coords = _world_bbox(obj)
    c = Vector((0.0,0.0,0.0))
    for p in coords: c += p
    c /= 8.0
    return c.x, c.y

def _world_bbox_size(obj):
    coords = _world_bbox(obj)
    xs = [c.x for c in coords]; ys = [c.y for c in coords]; zs = [c.z for c in coords]
    return (max(xs)-min(xs), max(ys)-min(ys), max(zs)-min(zs))

def _move_by(obj, dx=0.0, dy=0.0, dz=0.0):
    obj.location.x += dx
    obj.location.y += dy
    obj.location.z += dz

# ---------------------------
# Properties
# ---------------------------

def _poll_mesh(self, obj):
    return obj and obj.type == 'MESH'

class CYLSVG_Props(PropertyGroup):
    # object references
    flat_obj: PointerProperty(name="SVG/Mesh", type=bpy.types.Object, poll=_poll_mesh)
    cube_obj: PointerProperty(name="Base Cube", type=bpy.types.Object, poll=_poll_mesh)

    # store original planar X of SVG (meters) at import
    svg_orig_x: FloatProperty(name="Orig SVG X", default=0.0, min=0.0)

    # SVG solidify
    thk_value: FloatProperty(name="Thickness", default=2.0, min=0.0, precision=4)
    thk_unit: EnumProperty(name="Unit", items=[("mm","mm",""),("cm","cm",""),("m","m","")], default="mm")

    # Cylinder inputs (global unit)
    cyl_outer_r: FloatProperty(name="Outer Radius", default=0.050, min=0.0, precision=4)
    cyl_inner_r: FloatProperty(name="Inner Radius", default=0.040, min=0.0, precision=4)
    cyl_height:  FloatProperty(name="Height", default=0.100, min=0.0, precision=4)
    cyl_unit: EnumProperty(name="Unit", items=[("mm","mm",""),("cm","cm",""),("m","m","")], default="mm")

    cyl_subdiv: FloatProperty(name="Subdivisions", default=7.0, min=1.0, max=10.0, precision=0)

    # Appearance correction
    preserve_apparent_width: BoolProperty(
        name="Preserve Apparent Width",
        description="Pre-compress SVG in X so that, after wrapping, its front-view width equals the original flat width",
        default=True
    )

# ---------------------------
# Operators
# ---------------------------

class CYLSVG_OT_ImportSVG(Operator):
    bl_idname = "cylsvg.import_svg"
    bl_label = "Import SVG"
    bl_options = {'REGISTER','UNDO'}

    filepath: StringProperty(subtype="FILE_PATH")
    filter_glob: StringProperty(default="*.svg", options={'HIDDEN'})

    def execute(self, context):
        props = context.scene.cylsvg_props
        try:
            bpy.ops.preferences.addon_enable(module="io_curve_svg")
        except Exception:
            pass

        pre = set(o.name for o in bpy.data.objects)
        res = bpy.ops.import_curve.svg(filepath=self.filepath)
        if 'CANCELLED' in res:
            self.report({'ERROR'}, "SVG import cancelled or failed.")
            return {'CANCELLED'}

        context.view_layer.update()
        new_objs = [o for o in bpy.data.objects if o.name not in pre and o.type in {'CURVE','MESH'}]
        if not new_objs:
            self.report({'ERROR'}, "No SVG objects found.")
            return {'CANCELLED'}

        bpy.ops.object.select_all(action='DESELECT')
        for o in new_objs: o.select_set(True)
        context.view_layer.objects.active = new_objs[0]

        if any(o.type == 'CURVE' for o in new_objs):
            bpy.ops.object.convert(target='MESH', keep_original=False)

        meshes = [o for o in context.selected_objects if o.type == 'MESH']
        if not meshes:
            self.report({'ERROR'}, "Conversion failed.")
            return {'CANCELLED'}

        if len(meshes) > 1:
            bpy.ops.object.join()
            
        flat = context.view_layer.objects.active
        flat.name = "SVG_Flat"

        # Rotate SVG 180° around Y axis before applying modifiers
        flat.rotation_euler[1] = radians(180.0)
        bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)

        # store original planar X at import
        props.svg_orig_x = _world_bbox_size(flat)[0]

        # Solidify
        mod = flat.modifiers.new("CYLSVG_Thickness", 'SOLIDIFY')
        mod.thickness = _to_meters(props.thk_value, props.thk_unit)
        _apply_modifiers(flat)

        # Light subdiv (apply)
        _ensure_subsurf(flat, "CYLSVG_Subd", 4)
        _apply_modifiers(flat)

        # Origin to volume
        bpy.ops.object.select_all(action='DESELECT')
        flat.select_set(True)
        context.view_layer.objects.active = flat
        try:
            bpy.ops.object.origin_set(type='ORIGIN_CENTER_OF_VOLUME', center='MEDIAN')
        except Exception:
            pass

        props.flat_obj = flat
        self.report({'INFO'}, "SVG imported and converted to mesh.")
        return {'FINISHED'}

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

class CYLSVG_OT_AddCubeFromCylinder(Operator):
    """Create cube derived from cylinder dimensions (Outer R, Inner R, Height)"""
    bl_idname = "cylsvg.add_cube_from_cyl"
    bl_label = "Cylinder Dimensions"
    bl_options = {'REGISTER','UNDO'}

    def execute(self, context):
        p = context.scene.cylsvg_props

        outer = _to_meters(p.cyl_outer_r, p.cyl_unit)
        inner = _to_meters(p.cyl_inner_r, p.cyl_unit)
        height = _to_meters(p.cyl_height, p.cyl_unit)

        if inner >= outer:
            self.report({'ERROR'}, "Inner radius must be smaller than outer radius.")
            return {'CANCELLED'}

        # Map cylinder → cube
        X = 2 * pi * outer    # circumference
        Y = height
        Z = outer - inner     # wall thickness

        bpy.ops.mesh.primitive_cube_add(size=1.0, align='WORLD', location=(0,0,0))
        cube = context.view_layer.objects.active
        cube.name = "CYLSVG_Cube"
        cube.dimensions = (X, Y, Z)

        _ensure_subsurf(cube, "CYLSVG_CubeSubd", int(p.cyl_subdiv))
        _apply_modifiers(cube)
        p.cube_obj = cube

        self.report({'INFO'}, f"Cylinder→Cube: X={X:.3f}m, Y={Y:.3f}m, Z={Z:.3f}m (applied).")
        return {'FINISHED'}

class CYLSVG_OT_PlaceSvgOnCubeTopJoin(Operator):
    bl_idname = "cylsvg.place_svg_on_cube_join"
    bl_label = "Place SVG on Cube & Join"
    bl_options = {'REGISTER','UNDO'}

    def execute(self, context):
        p = context.scene.cylsvg_props
        flat, cube = p.flat_obj, p.cube_obj
        if not flat or not cube:
            self.report({'ERROR'}, "SVG or Cube not set.")
            return {'CANCELLED'}

        # Optional apparent-width preservation: pre-compress in X
        if p.preserve_apparent_width and p.svg_orig_x > 0:
            R = _to_meters(p.cyl_outer_r, p.cyl_unit)
            W_orig = p.svg_orig_x
            # Guard: chord can't exceed diameter
            max_chord = 2.0 * R
            if W_orig < max_chord and R > 0:
                # Find arc length L_target so chord = W_orig  =>  W_orig = 2R sin(L/(2R))
                # => L_target = 2R * asin(W_orig / (2R))
                L_target = 2.0 * R * asin(W_orig / (2.0 * R))
                cur_X = _world_bbox_size(flat)[0]
                if cur_X > 0:
                    flat.scale.x *= (L_target / cur_X)
                    context.view_layer.update()

        # Align XY centers
        cx, cy = _world_bbox_center_xy(cube)
        sx, sy = _world_bbox_center_xy(flat)
        _move_by(flat, dx=(cx - sx), dy=(cy - sy), dz=0.0)

        # Sit on top Z
        _, cube_top_z = _world_bbox_min_max_z(cube)
        svg_min_z, _ = _world_bbox_min_max_z(flat)
        _move_by(flat, dz=(cube_top_z - svg_min_z))

        # Join
        bpy.ops.object.select_all(action='DESELECT')
        cube.select_set(True); flat.select_set(True)
        context.view_layer.objects.active = cube
        bpy.ops.object.join()

        p.flat_obj = None
        p.cube_obj = context.view_layer.objects.active
        self.report({'INFO'}, "SVG aligned on cube and joined.")
        return {'FINISHED'}

class CYLSVG_OT_Cylindrify(Operator):
    bl_idname = "cylsvg.cylindrify"
    bl_label = "Cylindrify (361°)"
    bl_options = {'REGISTER','UNDO'}

    def execute(self, context):
        p = context.scene.cylsvg_props
        obj = p.cube_obj or context.active_object
        if not obj or obj.type != 'MESH':
            self.report({'ERROR'}, "No cube object found.")
            return {'CANCELLED'}

        bpy.ops.object.select_all(action='DESELECT')
        obj.select_set(True)
        context.view_layer.objects.active = obj

        obj.rotation_euler.x += radians(90.0)
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

        mod = obj.modifiers.new("CYLSVG_Bend", 'SIMPLE_DEFORM')
        mod.deform_method = 'BEND'
        mod.deform_axis = 'Z'
        mod.factor = radians(361.0)
        bpy.ops.object.modifier_apply(modifier=mod.name)

        self.report({'INFO'}, "Cylindrified (361° bend applied).")
        return {'FINISHED'}

# ---------------------------
# UI Panel
# ---------------------------

class CYLSVG_PT_Main(Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Cylindrify"
    bl_label = "Cylindrify"

    def draw(self, context):
        layout = self.layout
        p = context.scene.cylsvg_props

        # Import
        box = layout.box()
        box.label(text="Import SVG", icon="IMPORT")
        row = box.row(align=True)
        row.prop(p, "thk_value"); row.prop(p, "thk_unit", text="")
        box.operator("cylsvg.import_svg", icon="IMPORT")

        # Cylinder dimensions
        cyl_box = layout.box()
        cyl_box.label(text="Cylinder Dimensions", icon="MESH_CUBE")
        col = cyl_box.column(align=True)
        col.prop(p, "cyl_outer_r")
        col.prop(p, "cyl_inner_r")
        col.prop(p, "cyl_height")
        col.prop(p, "cyl_unit")
        col.prop(p, "cyl_subdiv")
        cyl_box.operator("cylsvg.add_cube_from_cyl", icon="MESH_CUBE")
        cyl_box.prop(p, "cube_obj", text="Base Cube")

        # Objects
        obj_box = layout.box()
        obj_box.label(text="Objects", icon="OUTLINER_OB_MESH")
        obj_box.prop(p, "flat_obj", text="SVG/Mesh")

        layout.prop(p, "preserve_apparent_width")
        layout.operator("cylsvg.place_svg_on_cube_join", icon="AUTOMERGE_ON")
        layout.operator("cylsvg.cylindrify", icon="MOD_SIMPLEDEFORM")

# ---------------------------
# Registration
# ---------------------------

classes = (
    CYLSVG_Props,
    CYLSVG_OT_ImportSVG,
    CYLSVG_OT_AddCubeFromCylinder,
    CYLSVG_OT_PlaceSvgOnCubeTopJoin,
    CYLSVG_OT_Cylindrify,
    CYLSVG_PT_Main,
)

def register():
    for c in classes: bpy.utils.register_class(c)
    bpy.types.Scene.cylsvg_props = PointerProperty(type=CYLSVG_Props)

def unregister():
    for c in reversed(classes): bpy.utils.unregister_class(c)
    del bpy.types.Scene.cylsvg_props

if __name__ == "__main__":
    register()
