// New in the ghackett fork of agent-session-manager (GPL-3.0).
// Portions adapted from joshedler/hunk-git-lite (MIT, © 2026 Josh Edler); see
// collins/THIRD_PARTY_LICENSES.md.

/**
 * The files panel, in place of hunk's own (`replaces: "hunk:files"`).
 *
 * The list hunk's pane would show — the loaded changeset's files in review
 * order with `+`/`-` counts, the selected one highlighted — with one
 * addition: while the working tree is loaded it splits into `UNSTAGED · n`
 * and `STAGED · n`. Hunk holds one of the two at a time, so the loaded
 * side is live (clicking a row selects the file) and the other side is
 * navigation (clicking a row, or its header, reloads to that side).
 *
 * Rendered defensively: a pane that throws while replacing `hunk:files`
 * is closed and hunk's own pane comes back, so every input may be empty.
 */

import { useEffect, useRef, useSyncExternalStore, type ReactNode } from "react";
import type { ScrollBoxRenderable } from "@opentui/core";
import type { ExtensionPaneProps } from "hunkdiff/extension";
import { fit } from "./commits.tsx";
import { filesSections, type FileRow, type Side } from "./model.ts";
import type { StatusCode } from "./git.ts";
import { paneHandlers, snapshotFiles, subscribeFiles } from "./store.ts";

type Theme = ExtensionPaneProps["theme"];

function codeColor(code: StatusCode, theme: Theme): string {
  switch (code) {
    case "?":
      return theme.fileUntracked;
    case "A":
      return theme.fileNew;
    case "D":
      return theme.fileDeleted;
    case "R":
    case "C":
      return theme.fileRenamed;
    case "U":
      return theme.badgeRemoved;
    default:
      return theme.fileModified;
  }
}

/** A row's element id, for `scrollChildIntoView`; the side keeps paths unique. */
function rowElementId(side: string, row: FileRow): string {
  return `file:${side}:${row.path}`;
}

function FileLine({
  row,
  side,
  width,
  theme,
  selected,
  onClick,
}: {
  row: FileRow;
  side: string;
  width: number;
  theme: Theme;
  selected: boolean;
  onClick: () => void;
}): ReactNode {
  const bg = selected ? theme.selectedHunk : theme.panel;
  const stats = row.binary
    ? "bin"
    : row.additions !== undefined && row.deletions !== undefined
      ? `+${row.additions} -${row.deletions}`
      : "";
  const label = row.previousPath !== undefined && row.previousPath !== row.path ? `${row.previousPath} → ${row.path}` : row.path;
  const pathWidth = Math.max(0, width - 3 - (stats === "" ? 0 : stats.length + 1));
  const path = fit(label, pathWidth).padEnd(pathWidth);
  const [added, removed] = stats.startsWith("+") ? stats.split(" ") : [stats, ""];
  return (
    <text id={rowElementId(side, row)} bg={bg} onMouseDown={onClick}>
      <span fg={codeColor(row.code, theme)} bg={bg}>{` ${row.code} `}</span>
      <span fg={theme.text} bg={bg}>
        {path}
      </span>
      {stats === "" ? null : (
        <span fg={row.binary ? theme.muted : theme.badgeAdded} bg={bg}>{` ${added}`}</span>
      )}
      {removed === "" ? null : <span fg={theme.badgeRemoved} bg={bg}>{` ${removed}`}</span>}
    </text>
  );
}

function Section({
  title,
  side,
  rows,
  emptyLabel,
  width,
  theme,
  selectedFileId,
  onHeader,
  onRow,
}: {
  title: string;
  side: string;
  rows: readonly FileRow[];
  emptyLabel: string;
  width: number;
  theme: Theme;
  selectedFileId: string | null;
  onHeader: (() => void) | null;
  onRow: (row: FileRow) => void;
}): ReactNode {
  return (
    <box style={{ width: "100%", flexDirection: "column", backgroundColor: theme.panel }}>
      <text
        id={`section:${side}`}
        fg={theme.accent}
        bg={theme.panelAlt}
        onMouseDown={onHeader === null ? undefined : () => onHeader()}
      >
        {fit(` ${title}`, width).padEnd(width)}
      </text>
      {rows.length === 0 ? (
        <text fg={theme.muted} bg={theme.panel}>
          {fit(` ${emptyLabel}`, width)}
        </text>
      ) : (
        rows.map((row) => (
          <FileLine
            key={`${side}:${row.path}`}
            row={row}
            side={side}
            width={width}
            theme={theme}
            selected={row.id !== null && row.id === selectedFileId}
            onClick={() => onRow(row)}
          />
        ))
      )}
    </box>
  );
}

export function FilesPane({ files, selectedFileId, width, height, theme, actions }: ExtensionPaneProps): ReactNode {
  const state = useSyncExternalStore(subscribeFiles, snapshotFiles);
  const sections = filesSections(state.status, files ?? [], state.loaded);
  const scroll = useRef<ScrollBoxRenderable | null>(null);

  useEffect(() => {
    if (selectedFileId === null) {
      return;
    }
    const selected = (files ?? []).find((file) => file.id === selectedFileId);
    if (selected !== undefined) {
      const side = sections.mode === "split" ? sections.live : "all";
      scroll.current?.scrollChildIntoView(`file:${side}:${selected.path}`);
    }
  }, [selectedFileId, files, sections.mode]);

  const selectLive = (row: FileRow) => {
    if (row.id !== null) {
      actions.selectFile(row.id);
      paneHandlers().selectedFile();
    }
  };
  const goTo = (side: Side, row: FileRow | null) => {
    paneHandlers().loadSide(side, row === null ? null : row.path, actions.notify);
  };

  let body: ReactNode;
  if (sections.mode === "flat") {
    body = (
      <Section
        title={`FILES · ${sections.rows.length}`}
        side="all"
        rows={sections.rows}
        emptyLabel="no files"
        width={width}
        theme={theme}
        selectedFileId={selectedFileId}
        onHeader={null}
        onRow={selectLive}
      />
    );
  } else {
    const live = sections.live;
    const sideProps = (side: Side, rows: readonly FileRow[], emptyLabel: string) => ({
      title: `${side.toUpperCase()} · ${rows.length}`,
      side,
      rows,
      emptyLabel,
      width,
      theme,
      selectedFileId,
      onHeader: side === live ? null : () => goTo(side, null),
      onRow: (row: FileRow) => (side === live ? selectLive(row) : goTo(side, row)),
    });
    body = (
      <>
        <Section {...sideProps("unstaged", sections.unstaged, "nothing unstaged")} />
        <text bg={theme.panel}> </text>
        <Section {...sideProps("staged", sections.staged, "nothing staged")} />
      </>
    );
  }

  return (
    <box
      style={{ width, height, overflow: "hidden", backgroundColor: theme.panel, flexDirection: "column" }}
    >
      {state.error !== null ? (
        <text fg={theme.muted} bg={theme.panel}>
          {fit(` ${state.error}`, width)}
        </text>
      ) : null}
      <scrollbox
        ref={scroll}
        focused={false}
        scrollY={true}
        style={{ flexGrow: 1, backgroundColor: theme.panel }}
        rootOptions={{ backgroundColor: theme.panel }}
        wrapperOptions={{ backgroundColor: theme.panel }}
        viewportOptions={{ backgroundColor: theme.panel }}
        contentOptions={{ backgroundColor: theme.panel }}
        verticalScrollbarOptions={{ visible: false }}
        horizontalScrollbarOptions={{ visible: false }}
      >
        {body}
      </scrollbox>
    </box>
  );
}
