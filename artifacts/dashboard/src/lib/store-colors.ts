export interface StoreColorSet {
  bg: string;
  text: string;
  border: string;
  dot: string;
  ring: string;
}

const PALETTE: StoreColorSet[] = [
  { bg: "bg-purple-500/15", text: "text-purple-400", border: "border-purple-500/25", dot: "bg-purple-400", ring: "ring-purple-500/30" },
  { bg: "bg-emerald-500/15", text: "text-emerald-400", border: "border-emerald-500/25", dot: "bg-emerald-400", ring: "ring-emerald-500/30" },
  { bg: "bg-blue-500/15", text: "text-blue-400", border: "border-blue-500/25", dot: "bg-blue-400", ring: "ring-blue-500/30" },
  { bg: "bg-orange-500/15", text: "text-orange-400", border: "border-orange-500/25", dot: "bg-orange-400", ring: "ring-orange-500/30" },
  { bg: "bg-pink-500/15", text: "text-pink-400", border: "border-pink-500/25", dot: "bg-pink-400", ring: "ring-pink-500/30" },
  { bg: "bg-teal-500/15", text: "text-teal-400", border: "border-teal-500/25", dot: "bg-teal-400", ring: "ring-teal-500/30" },
  { bg: "bg-yellow-500/15", text: "text-yellow-400", border: "border-yellow-500/25", dot: "bg-yellow-400", ring: "ring-yellow-500/30" },
  { bg: "bg-cyan-500/15", text: "text-cyan-400", border: "border-cyan-500/25", dot: "bg-cyan-400", ring: "ring-cyan-500/30" },
];

/** Returns a consistent color set for a store based on its numeric ID. */
export function getStoreColor(storeId: number): StoreColorSet {
  return PALETTE[(storeId - 1) % PALETTE.length];
}

/** Inline store badge component data for use in JSX. */
export function storeTag(storeId: number, storeName: string) {
  const c = getStoreColor(storeId);
  return { color: c, label: storeName };
}
