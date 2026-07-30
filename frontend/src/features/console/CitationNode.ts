/**
 * The `citation` TipTap node.
 *
 * This is the reason article bodies are stored as TipTap JSON rather than
 * markdown (DESIGN.md §3.4). As an atomic inline node, a citation:
 *
 *   - renders as a chip a reviewer can click to jump to the sources panel;
 *   - deletes as one unit, so it cannot be half-destroyed into "[S" — in
 *     markdown a reviewer backspacing through "[S1]" silently produces text
 *     that no longer parses as a citation and no longer resolves;
 *   - is countable structurally at approve time, which is what lets the server
 *     detect an orphaned or invented handle rather than regexing prose.
 *
 * `atom: true` plus `selectable: true` gives the delete-as-one-unit behaviour.
 */

import { Node, mergeAttributes } from "@tiptap/core";

export interface CitationAttrs {
  sourceIds: string[];
}

export const Citation = Node.create({
  name: "citation",
  group: "inline",
  inline: true,
  atom: true,
  selectable: true,
  // Not editable from within: the handles are data, not prose. A reviewer may
  // remove a citation, never retype one into existence.
  draggable: false,

  addAttributes() {
    return {
      sourceIds: {
        default: [] as string[],
        parseHTML: (element) => {
          const raw = element.getAttribute("data-source-ids") ?? "";
          return raw ? raw.split(",") : [];
        },
        renderHTML: (attributes) => ({
          "data-source-ids": (attributes.sourceIds as string[]).join(","),
        }),
      },
    };
  },

  parseHTML() {
    return [{ tag: "span[data-citation]" }];
  },

  renderHTML({ HTMLAttributes, node }) {
    const handles = (node.attrs.sourceIds as string[]) ?? [];
    return [
      "span",
      mergeAttributes(HTMLAttributes, {
        "data-citation": "",
        class:
          "mx-0.5 rounded bg-stone-100 px-1 py-0.5 align-baseline font-mono " +
          "text-[10px] text-stone-600 ring-1 ring-stone-200 cursor-pointer",
        title: `Source ${handles.join(", ")}`,
      }),
      handles.join(" "),
    ];
  },
});
