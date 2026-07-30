/**
 * TipTap editor over the draft.
 *
 * Loads `editedContent ?? originalContent` and saves only to edited content.
 * The `Citation` node makes citations first-class rather than prose (see
 * CitationNode.ts for why that matters).
 */

import { useEffect } from "react";
import { EditorContent, useEditor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";

import { Citation } from "./CitationNode";
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
      attributes: {
        class: "prose prose-stone max-w-none focus:outline-none",
      },
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
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-stone-200 px-4 py-2 text-xs text-stone-500">
        <span>Edits save to the reviewed copy — the AI draft is preserved</span>
        <SaveIndicator state={autosave.state} />
      </div>
      <EditorContent editor={editor} className="flex-1 overflow-y-auto p-4" />
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
    <span className={state === "error" ? "font-medium text-verdict-weak" : ""}>
      {labels[state]}
    </span>
  );
}
