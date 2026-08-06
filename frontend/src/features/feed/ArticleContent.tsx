/**
 * Renders a stored TipTap document as read-only article prose.
 *
 * A plain recursive walk rather than a read-only TipTap instance: the editor
 * bundle is large, and shipping it to every public reader to render static
 * text is a cost with no benefit. The console loads the real editor; the feed
 * does not.
 *
 * Citation nodes become bracketed chips — `[S1]` — that open the paper's card
 * in place. The design comp weighed three treatments and this is the one that
 * shipped: a superscript is a 4px tap target that vanishes at body size, and a
 * dotted underline on the sentence makes the *claim* look uncertain rather than
 * making its source available. The chip is the primary affordance for the thing
 * this product exists to let you do, so it is sized to be pressed.
 */

import { useEffect, useRef, useState } from "react";

import { STUDY_TYPE_LABELS } from "@/features/evidence/labels";
import {
  CHIP_ANCHOR,
  CITATION_CHIP,
  POPOVER_KICKER,
  POPOVER_LINK,
  POPOVER_META,
  POPOVER_TITLE,
  POPOVER_UNRESOLVED,
  PROSE_MEASURE,
  PROSE_PARAGRAPH,
  SOURCE_POPOVER,
} from "./styles";
import type { Source, TipTapDoc } from "@/types/api";

/** Citation nodes carry `attrs.sourceIds`; everything else is text. */
function sourceIds(attrs: Record<string, unknown> | undefined): string[] {
  const value = attrs?.sourceIds;
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];
}

interface Props {
  doc: TipTapDoc;
  sources: Source[];
  /**
   * Raised whenever a chip is pressed, so the source list can highlight the
   * matching row. The chip's `href` still anchors there — the highlight is what
   * makes the jump legible once you arrive.
   */
  onCite?: (handle: string) => void;
}

export function ArticleContent({ doc, sources, onCite }: Props) {
  // One popover at a time, keyed by paragraph + handle so the same source cited
  // twice does not open both cards.
  const [openKey, setOpenKey] = useState<string | null>(null);

  return (
    <div className={PROSE_MEASURE}>
      {(doc.content ?? []).map((paragraph, index) => (
        <p key={index} className={PROSE_PARAGRAPH}>
          {(paragraph.content ?? []).map((node, childIndex) =>
            node.type === "citation" ? (
              <CitationChips
                key={childIndex}
                handles={sourceIds(node.attrs)}
                sources={sources}
                keyPrefix={`${index}-${childIndex}`}
                openKey={openKey}
                onOpenChange={setOpenKey}
                onCite={onCite}
              />
            ) : (
              <span key={childIndex}>{node.text}</span>
            ),
          )}
        </p>
      ))}
    </div>
  );
}

function CitationChips({
  handles,
  sources,
  keyPrefix,
  openKey,
  onOpenChange,
  onCite,
}: {
  handles: string[];
  sources: Source[];
  keyPrefix: string;
  openKey: string | null;
  onOpenChange: (key: string | null) => void;
  onCite?: (handle: string) => void;
}) {
  if (handles.length === 0) return null;

  return (
    <>
      {handles.map((handle) => (
        <CitationChip
          key={handle}
          handle={handle}
          source={sources.find((s) => s.citationHandle === handle)}
          open={openKey === `${keyPrefix}-${handle}`}
          onOpenChange={(next) => onOpenChange(next ? `${keyPrefix}-${handle}` : null)}
          onCite={onCite}
        />
      ))}
    </>
  );
}

function CitationChip({
  handle,
  source,
  open,
  onOpenChange,
  onCite,
}: {
  handle: string;
  source: Source | undefined;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCite?: (handle: string) => void;
}) {
  const container = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (!open) return;

    function onPointerDown(event: MouseEvent) {
      if (!container.current?.contains(event.target as Node)) onOpenChange(false);
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onOpenChange(false);
    }

    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open, onOpenChange]);

  return (
    <span ref={container} className={CHIP_ANCHOR}>
      <a
        href={`#source-${handle}`}
        onClick={(event) => {
          // The anchor is the fallback, not the behaviour: opening the card in
          // place keeps the reader in the sentence they were checking. The href
          // still resolves for middle-click, and without JS.
          event.preventDefault();
          onOpenChange(!open);
          onCite?.(handle);
        }}
        aria-expanded={open}
        className={CITATION_CHIP}
      >
        {handle}
      </a>

      {open ? (
        <span className={SOURCE_POPOVER}>
          <span className={POPOVER_KICKER}>
            {handle}
            {source ? ` · ${STUDY_TYPE_LABELS[source.studyType]}` : ""}
          </span>

          {source ? (
            <>
              <span className={POPOVER_TITLE}>{source.title}</span>
              <span className={POPOVER_META}>
                {[source.journal, source.year].filter(Boolean).join(" · ")}
              </span>
              <a
                href={source.url}
                target="_blank"
                rel="noreferrer noopener"
                className={POPOVER_LINK}
              >
                Open the paper
              </a>
            </>
          ) : (
            // Validation guarantees every handle resolves before an article can
            // be published (invariant #2), so this is unreachable in the feed.
            // It exists because "silently render nothing" is the wrong way to
            // find out the guarantee has broken.
            <span className={POPOVER_UNRESOLVED}>
              This citation could not be matched to a source.
            </span>
          )}
        </span>
      ) : null}
    </span>
  );
}
