import type { Product } from "@/api/client";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";

/** The product photo as a tile that opens large over the blurred page. */
export function ProductLightbox({ product }: { product: Product }) {
  const alt = `${product.brand} ${product.name}`;

  return (
    <Dialog>
      <DialogTrigger asChild>
        <button
          aria-label={`View ${alt} larger`}
          className="group block aspect-square w-full cursor-pointer overflow-hidden rounded-md bg-muted outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
          type="button"
        >
          <img
            alt={alt}
            className="size-full object-cover transition-transform duration-300 group-hover:scale-[1.04]"
            loading="lazy"
            src={product.image_path}
          />
        </button>
      </DialogTrigger>
      <DialogContent className="w-[min(96vw,64rem)] p-3 sm:max-w-5xl">
        <DialogHeader className="pr-10">
          <DialogTitle>{alt}</DialogTitle>
          <DialogDescription className="sr-only">
            Product photo
          </DialogDescription>
        </DialogHeader>
        <img
          alt={alt}
          className="mx-auto max-h-[78dvh] w-auto rounded-md object-contain"
          src={product.image_path}
        />
      </DialogContent>
    </Dialog>
  );
}
