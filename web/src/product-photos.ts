/** The widths of the WebP copies stored beside each catalog JPEG. */
export const PHOTO_WIDTHS = [480, 960];

/** The `srcset` of a catalog photo: its WebP copies, so a small tile never loads the full JPEG. */
export function photoSrcSet(imagePath: string): string {
  if (!imagePath.endsWith(".jpg")) {
    throw new Error(`Catalog photos are JPEGs, not ${imagePath}`);
  }
  const stem = imagePath.slice(0, -".jpg".length);
  return PHOTO_WIDTHS.map(
    (width) => `${stem}-${String(width)}.webp ${String(width)}w`,
  ).join(", ");
}
