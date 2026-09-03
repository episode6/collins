// New in the ghackett fork of agent-session-manager (GPL-3.0).
// Portions adapted from sadick254/hunk-commit-log (MIT, © 2026 Sadick); see
// collins/THIRD_PARTY_LICENSES.md.

/**
 * The commits panel: one group per branch of interest, top to bottom —
 * the current branch (header, `working tree`, its own commits, `load
 * more…`), the parent branch, the default branch. The loaded row carries
 * `▸`, the row a reload is on its way to is tinted, unpushed commits carry
 * `↑`. A left click loads a row, a right click opens "Set parent branch…".
 *
 * Everything painted comes from store.ts; the pane holds no state of its
 * own because it unmounts whenever it is closed.
 */

import { useEffect, useRef, useSyncExternalStore, type ReactNode } from "react";
import type { ScrollBoxRenderable } from "@opentui/core";
import type { ExtensionPaneProps } from "hunkdiff/extension";
import type { Row } from "./model.ts";
import { paneHandlers, snapshotCommits, subscribeCommits } from "./store.ts";

/** Cut `text` to `width` columns, ellipsised; never wider, never negative. */
export function fit(text: string, width: number): string {
  if (width <= 0) {
    return "";
  }
  const chars = Array.from(text);
  if (chars.length <= width) {
    return chars.join("");
  }
  return width === 1 ? "…" : `${chars.slice(0, width - 1).join("")}…`;
}

interface MouseLike {
  button?: number;
}

function CommitsRow({
  row,
  width,
  theme,
  loaded,
  pending,
  active,
  notify,
}: {
  row: Row;
  width: number;
  theme: ExtensionPaneProps["theme"];
  loaded: boolean;
  pending: boolean;
  /** Whether the loaded row belongs to this header's group. */
  active: boolean;
  notify: ExtensionPaneProps["actions"]["notify"];
}): ReactNode {
  const onMouseDown = (event: MouseLike) => {
    const handlers = paneHandlers();
    if (event.button === 2) {
      handlers.contextRow(row);
    } else if (event.button === undefined || event.button === 0) {
      handlers.activateRow(row, notify);
    }
  };

  if (row.kind === "header") {
    const marker = loaded ? "▸" : " ";
    return (
      <text id={row.id} fg={active ? theme.accent : theme.muted} bg={theme.panelAlt} onMouseDown={onMouseDown}>
        {fit(`${marker}${row.label}`, width).padEnd(width)}
      </text>
    );
  }

  const bg = loaded ? theme.selectedHunk : pending ? theme.accentMuted : theme.panel;
  const fg = row.kind === "more" ? theme.muted : theme.text;
  const marker = loaded ? "▸" : " ";
  if (row.kind === "commit") {
    const up = row.unpushed ? "↑" : " ";
    const head = `${marker}${up}${row.abbrev ?? ""} `;
    const subject = fit(row.label, width - head.length);
    return (
      <text id={row.id} bg={bg} onMouseDown={onMouseDown}>
        <span fg={theme.muted} bg={bg}>{`${marker}${up}`}</span>
        <span fg={theme.accent} bg={bg}>{`${row.abbrev ?? ""} `}</span>
        <span fg={fg} bg={bg}>
          {subject.padEnd(Math.max(0, width - head.length))}
        </span>
      </text>
    );
  }
  return (
    <text id={row.id} fg={fg} bg={bg} onMouseDown={onMouseDown}>
      {fit(`${marker} ${row.label}`, width).padEnd(width)}
    </text>
  );
}

export function CommitsPane({ width, height, theme, actions }: ExtensionPaneProps): ReactNode {
  const { rows, loadedRowId, pendingRowId, error } = useSyncExternalStore(subscribeCommits, snapshotCommits);
  const scroll = useRef<ScrollBoxRenderable | null>(null);
  const followId = pendingRowId ?? loadedRowId;

  useEffect(() => {
    if (followId !== null) {
      scroll.current?.scrollChildIntoView(followId);
    }
  }, [followId, rows]);

  const loadedGroup = rows.find((row) => row.id === loadedRowId)?.group ?? null;

  return (
    <box
      style={{ width, height, overflow: "hidden", backgroundColor: theme.panel, flexDirection: "column" }}
    >
      {error !== null ? (
        <text fg={theme.muted} bg={theme.panel}>
          {fit(` ${error}`, width)}
        </text>
      ) : rows.length === 0 ? (
        <text fg={theme.muted} bg={theme.panel}>
          {fit(" no commits", width)}
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
        {rows.map((row) => (
          <CommitsRow
            key={row.id}
            row={row}
            width={width}
            theme={theme}
            loaded={row.id === loadedRowId}
            pending={row.id === pendingRowId}
            active={row.group === loadedGroup}
            notify={actions.notify}
          />
        ))}
      </scrollbox>
    </box>
  );
}
