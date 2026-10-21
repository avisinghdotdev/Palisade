import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router";

import type { Product } from "@/api/client";

import { productsOptions } from "@/api/client/@tanstack/react-query.gen";
import { AboutDialog } from "@/components/about-dialog";
import { ConsoleLogo } from "@/components/console-logo";
import { PalisadeBadge } from "@/components/palisade-badge";
import { ProductTile } from "@/components/product-tile";
import { ShopHeader } from "@/components/shop-header";
import { SurfaceLink } from "@/components/surface-link";
import { Button } from "@/components/ui/button";
import { useHydrated } from "@/hooks/use-hydrated";

import type { Route } from "./+types/shop";

export const links: Route.LinksFunction = () => [
  { rel: "icon", href: "/favicon.svg", type: "image/svg+xml" },
];

// The filter's display order; a category missing here cannot be filtered to.
const CATEGORIES = ["Road", "Tempo", "Trail", "Stability"];

interface Sort {
  id: string;
  label: string;
  compare: (a: Product, b: Product) => number;
}

const BY_BRAND: Sort = { id: "brand", label: "Brand A to Z", compare: () => 0 };
const SORTS: Sort[] = [
  BY_BRAND,
  {
    id: "price-asc",
    label: "Price: low to high",
    compare: (a, b) => Number(a.price.value) - Number(b.price.value),
  },
  {
    id: "price-desc",
    label: "Price: high to low",
    compare: (a, b) => Number(b.price.value) - Number(a.price.value),
  },
];

export const meta: Route.MetaFunction = () => [
  { title: "Ridgeline Running" },
  {
    name: "description",
    content:
      "Running shoes from Nike, Adidas, Hoka, Brooks, New Balance, and Asics.",
  },
];

interface FilterOption {
  value: string;
  count: number;
}

/** A query value that matches no option, from an old link or a typo, counts as "All". */
function pick(options: string[], value: null | string): null | string {
  return value !== null && options.includes(value) ? value : null;
}

function FilterGroup({
  id,
  label,
  options,
  selected,
  onSelect,
}: {
  id: string;
  label: string;
  options: FilterOption[];
  selected: null | string;
  onSelect: (value: null | string) => void;
}) {
  return (
    <div aria-labelledby={id} role="radiogroup">
      <p className="mb-2 text-sm font-semibold" id={id}>
        {label}
      </p>
      <div className="flex flex-wrap gap-2 lg:flex-col lg:items-start">
        <Button
          aria-checked={selected === null}
          onClick={() => {
            onSelect(null);
          }}
          role="radio"
          size="sm"
          variant={selected === null ? "default" : "outline"}
        >
          All
        </Button>
        {options.map(({ value, count }) => {
          const unavailable = count === 0 && selected !== value;
          return (
            <Button
              aria-checked={selected === value}
              aria-disabled={unavailable}
              className={unavailable ? "opacity-50" : undefined}
              key={value}
              onClick={() => {
                if (!unavailable) onSelect(value);
              }}
              role="radio"
              size="sm"
              variant={selected === value ? "default" : "outline"}
            >
              {value}
              <span className="font-normal text-current/70">{count}</span>
            </Button>
          );
        })}
      </div>
    </div>
  );
}

export default function Shop() {
  const products = useQuery(productsOptions());
  const [params, setParams] = useSearchParams();
  // The prerendered page knows no query string, so the filters apply only once hydrated.
  const hydrated = useHydrated();
  const all = products.data ?? [];
  const brandNames = [...new Set(all.map((product) => product.brand))].sort();
  const category = pick(CATEGORIES, hydrated ? params.get("category") : null);
  const brand = pick(brandNames, hydrated ? params.get("brand") : null);
  const sortId = hydrated ? params.get("sort") : null;
  const sort = SORTS.find((option) => option.id === sortId) ?? BY_BRAND;

  const setParam = (key: string, value: null | string) => {
    setParams(
      (current) => {
        if (value === null) current.delete(key);
        else current.set(key, value);
        return current;
      },
      { replace: true },
    );
  };

  // Each filter counts only what the other one leaves: no trail shoes when the brand has none.
  const categories = CATEGORIES.map((value) => ({
    value,
    count: all.filter(
      (product) =>
        product.category === value &&
        (brand === null || product.brand === brand),
    ).length,
  }));
  const brands = brandNames.map((value) => ({
    value,
    count: all.filter(
      (product) =>
        product.brand === value &&
        (category === null || product.category === category),
    ).length,
  }));
  const shown = all
    .filter(
      (product) =>
        (category === null || product.category === category) &&
        (brand === null || product.brand === brand),
    )
    .toSorted(sort.compare);

  return (
    <div className="theme-shop flex min-h-dvh flex-col bg-background text-foreground">
      <ShopHeader to="/shop">
        <PalisadeBadge>Protected by Palisade</PalisadeBadge>
        <SurfaceLink icon={<ConsoleLogo />} to="/merchant">
          Merchant console
        </SurfaceLink>
        <AboutDialog title="About Shop">
          <p>
            Ridgeline Running is a demo shop whose checkout AI shopping agents
            can use. Nothing is for sale. Palisade verifies each agent, records
            the buyer's signed mandate on the PayPal order, and answers disputes
            with that evidence.
          </p>
          <p>
            The shoes are real models, named so you can recognize them. The
            names and logos belong to their makers, who have no part in this
            project. Photos are from Unsplash under the Unsplash License.
          </p>
          <p>
            <Link
              className="underline underline-offset-2"
              rel="noreferrer"
              target="_blank"
              to="/credits"
            >
              Photo credits and license
              <span className="sr-only"> (opens in a new tab)</span>
            </Link>
          </p>
        </AboutDialog>
      </ShopHeader>
      <main className="flex-1 px-4 py-8 sm:px-6 lg:px-8">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <h1 className="text-2xl font-semibold tracking-tight">
            Running shoes
          </h1>
          <div className="flex items-center gap-2 text-sm">
            <label className="text-muted-foreground" htmlFor="sort">
              Sort by
            </label>
            <select
              className="rounded-md border bg-card px-2 py-1.5 outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
              id="sort"
              onChange={(event) => {
                setParam(
                  "sort",
                  event.target.value === BY_BRAND.id
                    ? null
                    : event.target.value,
                );
              }}
              value={sort.id}
            >
              {SORTS.map((option) => (
                <option key={option.id} value={option.id}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>
        </div>
        <p aria-live="polite" className="sr-only">
          {products.data && `${String(shown.length)} shoes shown`}
        </p>
        <div className="mt-6 grid gap-8 lg:grid-cols-[12rem_1fr]">
          <aside aria-label="Filters" className="space-y-6">
            <FilterGroup
              id="filter-category"
              label="Category"
              onSelect={(value) => {
                setParam("category", value);
              }}
              options={categories}
              selected={category}
            />
            <FilterGroup
              id="filter-brand"
              label="Brand"
              onSelect={(value) => {
                setParam("brand", value);
              }}
              options={brands}
              selected={brand}
            />
          </aside>
          <section aria-label="Products">
            {products.isPending && (
              <p className="text-sm text-muted-foreground">Loading…</p>
            )}
            {products.isError && (
              <p className="flex items-center gap-3 text-sm">
                The catalog did not load.
                <Button
                  onClick={() => {
                    void products.refetch();
                  }}
                  size="sm"
                  variant="outline"
                >
                  Try again
                </Button>
              </p>
            )}
            {products.data && shown.length === 0 && (
              <p className="text-sm text-muted-foreground">
                No shoes match these filters.
              </p>
            )}
            {shown.length > 0 && (
              <ul className="grid grid-cols-2 gap-x-4 gap-y-8 md:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5">
                {shown.map((product) => (
                  <li key={product.id}>
                    <ProductTile product={product} />
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      </main>
    </div>
  );
}
