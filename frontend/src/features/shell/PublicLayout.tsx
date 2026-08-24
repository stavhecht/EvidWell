/**
 * The public group's shell: site bar above, tab bar below, route in between.
 *
 * Under the Next.js port this is `app/(public)/layout.tsx` verbatim, with
 * `<Outlet/>` becoming `{children}`.
 *
 * Rendered as a layout route rather than above `<Routes>` so the review desk
 * does not inherit it — the header is the public site's, and a reviewer working
 * through a queue should not be looking at a search box for articles they have
 * not published yet.
 */

import { useEffect } from "react";
import { Outlet, useLocation } from "react-router-dom";

import { MobileTabBar } from "./MobileTabBar";
import { SiteHeader } from "./SiteHeader";

export function PublicLayout() {
  const { pathname } = useLocation();

  /*
   * Scroll to the top on navigation.
   *
   * The browser restores scroll position on Back, which is right, but a fresh
   * push keeps whatever offset the previous page had — so opening an article
   * from halfway down the feed lands you halfway down the article. Keyed on
   * `pathname` only, deliberately: the search string changes when a filter is
   * applied, and yanking the reader to the top for that would be hostile.
   */
  useEffect(() => {
    window.scrollTo(0, 0);
  }, [pathname]);

  return (
    <>
      <SiteHeader />
      <Outlet />
      <MobileTabBar />
    </>
  );
}
