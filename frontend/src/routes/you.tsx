/**
 * Your profile: interests, folders, and the shelf.
 *
 * Signed out this is a pitch rather than a redirect. Bouncing a guest to
 * `/join` would lose the one thing that makes signing up make sense — seeing
 * what the page would hold.
 *
 * The interests here are the same control as on the sign-up form and write
 * through the same PATCH. Changing them invalidates the feed, because the
 * ordering is computed server-side and the cached pages are stale the moment
 * they change (see `ReaderAuthProvider.update`).
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { ArticleCard } from "@/features/feed/ArticleCard";
import { SKELETON_GRID, SKELETON_TILE } from "@/features/feed/styles";
import { SUBJECTS, SUBJECT_LABELS } from "@/features/evidence/subject";
import { readerInitials, useReader } from "@/features/reader/auth";
import { useToast } from "@/features/shell/toast";
import {
  CHIP_COUNT,
  CHIP_ROW,
  EMPTY_SHELF,
  EMPTY_SHELF_BODY,
  EMPTY_SHELF_TITLE,
  FIELD_LABEL,
  GUEST_BODY,
  GUEST_CARD,
  GUEST_TITLE,
  NEW_FOLDER_ACTION,
  NEW_FOLDER_INPUT,
  NEW_FOLDER_ROW,
  PAGE_BACK_LINK,
  PROFILE_AVATAR,
  PROFILE_HEAD,
  PROFILE_IDENTITY,
  PROFILE_META,
  PROFILE_NAME,
  WIDE_PAGE,
  chip,
} from "@/features/shell/pageStyles";
import { PILL_BUTTON } from "@/features/feed/styles";
import { createFolder, fetchSaved, readerKeys } from "@/lib/api/reader";
import { ApiError } from "@/lib/api/client";
import type { Subject } from "@/types/api";

export function YouRoute() {
  const { reader, status, signedIn, update, logout } = useReader();
  const [folderId, setFolderId] = useState<string | null>(null);
  const [newFolder, setNewFolder] = useState("");
  const flash = useToast();
  const queryClient = useQueryClient();

  const saved = useQuery({
    queryKey: readerKeys.savedIn(folderId),
    queryFn: () => fetchSaved(folderId),
    enabled: signedIn,
  });

  const addFolder = useMutation({
    mutationFn: (name: string) => createFolder(name),
    onSuccess: (folder) => {
      setNewFolder("");
      setFolderId(folder.id);
      flash(`Folder “${folder.name}” created`);
      void queryClient.invalidateQueries({ queryKey: readerKeys.saved });
      void queryClient.invalidateQueries({ queryKey: readerKeys.folders });
    },
    onError: (caught) =>
      flash(
        caught instanceof ApiError && caught.status === 409
          ? "You already have a folder with that name"
          : "Could not create that folder",
      ),
  });

  if (status === "loading") return null;

  if (!signedIn) return <GuestState />;

  const folders = saved.data?.folders ?? [];
  const items = saved.data?.items ?? [];

  async function toggleInterest(subject: Subject) {
    const current = reader?.interests ?? [];
    const next = current.includes(subject)
      ? current.filter((value) => value !== subject)
      : [...current, subject];
    await update({ interests: next });
    flash(
      next.length
        ? `Your feed leads with ${next.length} ${next.length === 1 ? "subject" : "subjects"}`
        : "Personalisation off — newest first",
    );
  }

  return (
    <main className={WIDE_PAGE}>
      <Link to="/" className={PAGE_BACK_LINK}>
        ← Feed
      </Link>

      <div className={PROFILE_HEAD}>
        <div className={PROFILE_IDENTITY}>
          <span className={PROFILE_AVATAR}>{readerInitials(reader)}</span>
          <div>
            <h1 className={PROFILE_NAME}>{reader?.displayName}</h1>
            <div className={PROFILE_META}>
              {reader?.interests.length
                ? reader.interests.map((s) => SUBJECT_LABELS[s]).join(" · ")
                : "No interests picked yet"}
              {reader?.newsletter ? " — newsletter on" : ""}
            </div>
          </div>
        </div>

        <button onClick={logout} className={PILL_BUTTON}>
          Sign out
        </button>
      </div>

      <div className={FIELD_LABEL}>Interests</div>
      <div className={`${CHIP_ROW} mb-8`}>
        {SUBJECTS.map((subject) => {
          const on = reader?.interests.includes(subject) ?? false;
          return (
            <button
              key={subject}
              onClick={() => void toggleInterest(subject)}
              aria-pressed={on}
              className={chip(on)}
            >
              {SUBJECT_LABELS[subject]}
            </button>
          );
        })}
      </div>

      <div className={FIELD_LABEL}>Folders</div>
      <div className={CHIP_ROW}>
        <button
          onClick={() => setFolderId(null)}
          aria-pressed={folderId === null}
          className={chip(folderId === null)}
        >
          <span>Everything</span>
          <span className={CHIP_COUNT}>
            {folders.reduce((total, folder) => total + folder.count, 0)}
          </span>
        </button>
        {folders.map((folder) => (
          <button
            key={folder.id}
            onClick={() => setFolderId(folder.id)}
            aria-pressed={folderId === folder.id}
            className={chip(folderId === folder.id)}
          >
            <span>{folder.name}</span>
            <span className={CHIP_COUNT}>{folder.count}</span>
          </button>
        ))}
      </div>

      <div className={NEW_FOLDER_ROW}>
        <input
          value={newFolder}
          onChange={(event) => setNewFolder(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && newFolder.trim()) {
              addFolder.mutate(newFolder.trim());
            }
          }}
          placeholder="New folder name"
          aria-label="New folder name"
          className={NEW_FOLDER_INPUT}
        />
        <button
          onClick={() => newFolder.trim() && addFolder.mutate(newFolder.trim())}
          disabled={!newFolder.trim() || addFolder.isPending}
          className={NEW_FOLDER_ACTION}
        >
          + Add folder
        </button>
      </div>

      {saved.status === "pending" ? (
        <div className={SKELETON_GRID} aria-busy="true">
          {[220, 190, 250, 200].map((height, index) => (
            <div key={index} style={{ height }} className={SKELETON_TILE} />
          ))}
        </div>
      ) : items.length === 0 ? (
        <div className={EMPTY_SHELF}>
          <p className={EMPTY_SHELF_TITLE}>
            {folderId ? "This folder is empty." : "Nothing saved yet."}
          </p>
          <p className={EMPTY_SHELF_BODY}>
            Press Save on any tile in the feed and it lands in the folder you
            have open.
          </p>
        </div>
      ) : (
        /*
         * A plain CSS grid rather than the feed's virtualised masonry. A shelf
         * is a human-sized list — there is no infinite scroll to virtualise —
         * and masonic measures nodes on mount, which makes it the wrong tool
         * for a list that shrinks as you remove things from it.
         */
        <div className="grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-3.5">
          {items.map((card) => (
            <ArticleCard key={card.slug} card={card} />
          ))}
        </div>
      )}
    </main>
  );
}

function GuestState() {
  return (
    <main className={WIDE_PAGE}>
      <Link to="/" className={PAGE_BACK_LINK}>
        ← Feed
      </Link>

      <div className={PROFILE_HEAD}>
        <div className={PROFILE_IDENTITY}>
          <span className={PROFILE_AVATAR}>?</span>
          <div>
            <h1 className={PROFILE_NAME}>Guest</h1>
            <div className={PROFILE_META}>Not signed in</div>
          </div>
        </div>
      </div>

      <div className={GUEST_CARD}>
        <p className={GUEST_TITLE}>You are browsing as a guest.</p>
        <p className={GUEST_BODY}>
          Everything on the feed is readable without an account. An account
          keeps your folders across devices and moves the subjects you care
          about to the top — without hiding anything else.
        </p>
        <Link to="/join" className={PILL_BUTTON}>
          Create an account
        </Link>
      </div>
    </main>
  );
}
