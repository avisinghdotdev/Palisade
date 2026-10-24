import { describe, expect, test } from "vitest";

import { PHOTO_WIDTHS, photoSrcSet } from "@/product-photos";

const files = Object.keys(import.meta.glob("/public/products/*")).map((path) =>
  path.replace("/public", ""),
);

describe("photoSrcSet", () => {
  test("lists each WebP copy with its width", () => {
    expect(photoSrcSet("/products/hoka-mach-x-2.jpg")).toBe(
      "/products/hoka-mach-x-2-480.webp 480w, /products/hoka-mach-x-2-960.webp 960w",
    );
  });

  test("refuses a photo that is not a JPEG", () => {
    expect(() => photoSrcSet("/products/hoka-mach-x-2.png")).toThrow();
  });
});

test("every catalog photo has its WebP copies", () => {
  const photos = files.filter((file) => file.endsWith(".jpg"));

  expect(photos).not.toHaveLength(0);
  for (const photo of photos) {
    for (const width of PHOTO_WIDTHS) {
      expect(files).toContain(
        photo.replace(/\.jpg$/, `-${String(width)}.webp`),
      );
    }
  }
});
