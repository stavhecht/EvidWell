/**
 * TipTap editor over the draft.
 *
 * Loads `editedContent ?? originalContent` and saves only to edited content.
 * The `Citation` node makes citations first-class rather than prose (see
 * CitationNode.ts for why that matters).
 *
 * The editing surface is drawn as a recessed panel on the ground with a rule
 * around it — the same inversion the console's text fields use. In a system
 * with no corner radius, the border is the only thing that can say "this region
 * is yours to change", and the reviewer needs to know that at a glance on a
 * screen where everything else is read-only.
 */

import { useEffect } from "react";
import { EditorContent, useEditor, type Editor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";

import { Citation } from "./CitationNode";
import {
  EDITOR_NOTE,
  EDITOR_PROSE,
  EDITOR_STATUS_ROW,
  EDITOR_SURFACE,
  TOOLBAR,
  saveIndicator,
  toolbarButton,
} from "./styles";
import type { Autosave } from "./useAutosave";
import type { TipTapDoc } from "@/types/api";

interface Props {
  content: TipTapDoc;
  autosave: Autosave;
  /** Clicking a citation chip scrolls the sources panel to that handle. */
  onCitationClick?: (handle: string) => void;
}

export function ArticleEditor({ content, autosave, onCitationClick }: Props) {
  const editor = useEditor({
    extensions: [
      StarterKit.configure({
        // The article is three paragraphs of prose. Headings, lists and code
        // blocks are not part of the format, and offering them invites edits
        // the public renderer has no way to display.
        heading: false,
        codeBlock: false,
        bulletList: false,
        orderedList: false,
        blockquote: false,
      }),
      Citation,
    ],
    content,
    editorProps: {
      attributes: { class: EDITOR_PROSE },
      handleClick(_view, _pos, event) {
        const target = (event.target as HTMLElement).closest("[data-citation]");
        if (!target || !onCitationClick) return false;
        const handles = (target.getAttribute("data-source-ids") ?? "").split(",");
        if (handles[0]) onCitationClick(handles[0]);
        return true;
      },
    },
    onUpdate: ({ editor }) => autosave.schedule(editor.getJSON() as TipTapDoc),
  });

  // Swapping to a different article must replace the document, or the previous
  // draft's text stays in the editor and autosaves onto the new article.
  useEffect(() => {
    if (editor && content) {
      // emitUpdate=false: loading a document must not look like an edit, or
      // opening a draft immediately autosaves it back over itself.
      editor.commands.setContent(content, false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editor, content]);

  return (
    <div>
      <Toolbar editor={editor} />
      <EditorContent editor={editor} className={EDITOR_SURFACE} />
      <div className={EDITOR_STATUS_ROW}>
        <span className={EDITOR_NOTE}>
          Edits save to the reviewed copy — the AI draft is preserved.
        </span>
        <SaveIndicator state={autosave.state} />
      </div>
    </div>
  );
}

/**
 * Bold and italic only.
 *
 * The design comp draws four buttons — the two here plus Quote and Insert
 * citation — but both of those would be controls that break something. Quote is
 * disabled in StarterKit above because the public renderer has no blockquote
 * case, and hand-inserting a citation is how a handle that resolves to nothing
 * gets into a draft, which is precisely what validation exists to reject
 * (invariant #2). A citation picker driven by the retrieved sources is the
 * shape that would work; it is not this.
 */
function Toolbar({ editor }: { editor: Editor | null }) {
  if (!editor) return null;

  const marks = [
    { name: "bold" as const, label: "Bold", run: () => editor.chain().focus().toggleBold().run() },
    { name: "italic" as const, label: "Italic", run: () => editor.chain().focus().toggleItalic().run() },
  ];

  return (
    <div className={TOOLBAR}>
      {marks.map((mark) => (
        <button
          key={mark.name}
          type="button"
          onClick={mark.run}
          aria-pressed={editor.isActive(mark.name)}
          className={toolbarButton(editor.isActive(mark.name))}
        >
          {mark.label}
        </button>
      ))}
    </div>
  );
}

function SaveIndicator({ state }: { state: Autosave["state"] }) {
  const labels: Record<Autosave["state"], string> = {
    idle: "",
    saving: "Saving…",
    saved: "Saved",
    error: "Save failed — retrying",
  };
  return (
    <span className={saveIndicator(state === "error")}>
      {labels[state]}
    </span>
  );
}
