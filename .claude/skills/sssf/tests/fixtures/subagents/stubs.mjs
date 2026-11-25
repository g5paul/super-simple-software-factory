// Stand-ins for what subagents.ts imports from pi, typebox and themeMap.ts.
// Only the names the file uses; none of it draws anything.
export const StringEnum = (values) => ({ enum: values });
export const Type = {
  Object: (props) => ({ props }),
  String: () => ({}),
  Number: () => ({}),
  Optional: (x) => x,
};
export class DynamicBorder {}
export class Container {
  addChild() {}
  render() { return []; }
  invalidate() {}
}
export class Text {
  setText() {}
}
export function applyExtensionDefaults() {}
