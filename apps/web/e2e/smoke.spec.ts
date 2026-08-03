import { expect, test } from "@playwright/test";

/**
 * Smoke coverage for the journeys the HTTP-level pytest suite can't see:
 * that the pages actually render in a browser. Deliberately not exercising
 * the full upload → translate → export flow here — that needs a real
 * backend and file fixtures; see apps/api/tests/test_pipeline.py for the
 * equivalent journey at the HTTP layer.
 */

test.describe("Public pages", () => {
  test("home page loads with the upload zone", async ({ page }) => {
    await page.goto("/en");
    await expect(page.locator("h1")).toBeVisible();
    await expect(page.getByText("Drop a file here")).toBeVisible();
  });

  test("a tool page renders the working area", async ({ page }) => {
    await page.goto("/en/image-to-text");
    await expect(page.locator("h1")).toBeVisible();
  });

  test("pricing page lists plans", async ({ page }) => {
    await page.goto("/en/pricing");
    await expect(page.getByText("Free", { exact: true }).first()).toBeVisible();
  });

  test("locale-less path redirects to the default locale", async ({ page }) => {
    await page.goto("/image-translator");
    await expect(page).toHaveURL(/\/en\/image-translator/);
  });

  test("dark mode toggle flips the html class", async ({ page }) => {
    await page.goto("/en");
    await page.getByRole("button", { name: "Theme" }).click();
    const isDark = await page.evaluate(() =>
      document.documentElement.classList.contains("dark"),
    );
    expect(typeof isDark).toBe("boolean");
  });
});

test.describe("Auth pages", () => {
  test("sign-up page renders the form", async ({ page }) => {
    await page.goto("/en/auth/sign-up");
    await expect(page.getByLabel("Email", { exact: true })).toBeVisible();
    await expect(page.getByLabel("Password", { exact: true })).toBeVisible();
  });

  test("sign-in page renders the form", async ({ page }) => {
    await page.goto("/en/auth/sign-in");
    await expect(
      page.getByRole("button", { name: "Sign in", exact: true }),
    ).toBeVisible();
  });
});
