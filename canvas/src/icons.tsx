// Inline SVG icons for the canvas toolbar. No icon-font dependency: these ship in the
// bundle as tiny stroke paths, render crisply on every OS (the old Unicode glyphs did
// not), and inherit `currentColor` + size from their button. 16px default, 1.6 stroke.

type IconProps = { size?: number };

function Svg({ size = 16, children }: IconProps & { children: React.ReactNode }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.7}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {children}
    </svg>
  );
}

// --- create ---------------------------------------------------------------------
export const IconPlus = (p: IconProps) => (
  <Svg {...p}>
    <path d="M12 5v14M5 12h14" />
  </Svg>
);

// --- panels ---------------------------------------------------------------------
export const IconOntology = (p: IconProps) => (
  <Svg {...p}>
    <path d="M12 3l7.5 4.5v9L12 21l-7.5-4.5v-9L12 3z" />
  </Svg>
);
export const IconLayers = (p: IconProps) => (
  <Svg {...p}>
    <path d="M12 3l9 5-9 5-9-5 9-5z" />
    <path d="M3 12l9 5 9-5" />
    <path d="M3 16l9 5 9-5" />
  </Svg>
);
export const IconChanges = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="6" cy="6" r="2.4" />
    <circle cx="6" cy="18" r="2.4" />
    <circle cx="18" cy="18" r="2.4" />
    <path d="M6 8.4v7.2M8.4 6H14a3 3 0 0 1 3 3v6.6" />
  </Svg>
);
export const IconDecisions = (p: IconProps) => (
  <Svg {...p}>
    <path d="M12 4v16M6 8h12" />
    <path d="M6 8l-2.5 5a2.5 2.5 0 0 0 5 0L6 8zM18 8l-2.5 5a2.5 2.5 0 0 0 5 0L18 8z" />
    <path d="M9 20h6" />
  </Svg>
);

// --- view -----------------------------------------------------------------------
export const IconTypes = (p: IconProps) => (
  <Svg {...p}>
    <path d="M8 7l-4 5 4 5M16 7l4 5-4 5" />
  </Svg>
);
export const IconCollapse = (p: IconProps) => (
  <Svg {...p}>
    <path d="M8 8l4-4 4 4M8 16l4 4 4-4" />
    <path d="M4 12h16" />
  </Svg>
);
export const IconLayout = (p: IconProps) => (
  <Svg {...p}>
    <rect x="3" y="4" width="7" height="7" rx="1" />
    <rect x="14" y="4" width="7" height="7" rx="1" />
    <rect x="3" y="15" width="7" height="5" rx="1" />
    <rect x="14" y="15" width="7" height="5" rx="1" />
  </Svg>
);
export const IconFit = (p: IconProps) => (
  <Svg {...p}>
    <path d="M4 8V5a1 1 0 0 1 1-1h3M16 4h3a1 1 0 0 1 1 1v3M20 16v3a1 1 0 0 1-1 1h-3M8 20H5a1 1 0 0 1-1-1v-3" />
  </Svg>
);
export const IconCaret = (p: IconProps) => (
  <Svg {...p}>
    <path d="M6 9l6 6 6-6" />
  </Svg>
);

// --- data -----------------------------------------------------------------------
export const IconRefresh = (p: IconProps) => (
  <Svg {...p}>
    <path d="M20 11a8 8 0 1 0-.5 4" />
    <path d="M20 4v5h-5" />
  </Svg>
);
export const IconInterchange = (p: IconProps) => (
  <Svg {...p}>
    <path d="M4 8h13l-3-3M20 16H7l3 3" />
  </Svg>
);

// --- status ---------------------------------------------------------------------
export const IconEntity = (p: IconProps) => (
  <Svg {...p}>
    <rect x="4" y="4" width="16" height="16" rx="2" />
    <path d="M4 9h16" />
  </Svg>
);
export const IconRelationship = (p: IconProps) => (
  <Svg {...p}>
    <path d="M4 7h9a4 4 0 0 1 4 4v3" />
    <path d="M14 11l3 3 3-3" />
  </Svg>
);
export const IconCheck = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="9" />
    <path d="M8.5 12.5l2.5 2.5 4.5-5" />
  </Svg>
);
export const IconWarn = (p: IconProps) => (
  <Svg {...p}>
    <path d="M12 4l9 16H3l9-16z" />
    <path d="M12 10v4M12 17.5v.5" />
  </Svg>
);
export const IconError = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="9" />
    <path d="M15 9l-6 6M9 9l6 6" />
  </Svg>
);
