import { render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AppHeader } from "./app-header";

afterEach(() => vi.unstubAllGlobals());

it("renders one header.topbar with the brand, project, menu, API status and actions", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({ status: "ok", service: "reframe-api" }) } as Response)));
  const { container } = render(<AppHeader project="My video · Customize" menu={<details><summary>Saved projects</summary></details>} actions={<button>Return to result</button>} />);
  expect(container.querySelectorAll("header.topbar")).toHaveLength(1);
  const header = screen.getByRole("banner");
  expect(header).toHaveTextContent("ReFrame.");
  expect(header).toHaveTextContent("My video · Customize");
  expect(screen.getByText("Saved projects")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Return to result" })).toBeInTheDocument();
  expect(await screen.findByText("API connected")).toBeInTheDocument();
});
