/**
 * Renders a stored TipTap document as read-only article prose.
 *
 * A plain recursive walk rather than a read-only TipTap instance: the editor
 * bundle is large, and shipping it to every public reader to render static
 * text is a cost with no benefit. The console loads the real editor; the feed
 * does not.
 *
 * Citation nodes become superscript anchors into the source list below.
 */

import type { TipTapDoc } from "@/types/api";

/** Citation nodes carry `attrs.sourceIds`; everything else is text. */
function sourceIds(attrs: Record<string, unknown> | undefined): string[] {
  const value = attrs?.sourceIds;
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];
}

export function ArticleContent({ doc }: { doc: TipTapDoc }) {
  return (
    <div className="mt-6 space-y-4 text-stone-800">
      {(doc.content ?? []).map((paragraph, index) => (
        <p key={index} className="leading-relaxed">
          {(paragraph.content ?? []).map((node, childIndex) =>
            node.type === "citation" ? (
              <CitationMarker key={childIndex} handles={sourceIds(node.attrs)} />
            ) : (
              <span key={childIndex}>{node.text}</span>
            ),
          )}
        </p>
      ))}
    </div>
  );
}

/**
 * A superscript link per handle, e.g. ¹ ³.
 *
 * Anchors to `#source-S1` in the list below rather than opening the paper
 * directly: the reader should see what kind of study it is before deciding to
 * leave the page.
 */
function CitationMarker({ handles }: { handles: string[] }) {
  if (handles.length === 0) return null;

  return (
    <sup className="ml-0.5 inline-flex gap-0.5">
      {handles.map((handle) => (
        <a
          key={handle}
          href={`#source-${handle}`}
          className="rounded px-0.5 text-xs font-medium text-stone-500 underline decoration-dotted hover:bg-stone-100 hover:text-stone-900"
          title={`Jump to source ${handle}`}
        >
          {handle}
        </a>
      ))}
    </sup>
  );
}
