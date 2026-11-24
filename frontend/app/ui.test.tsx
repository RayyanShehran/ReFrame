import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { Badge, Banner, Button, EmptyState, Field, Panel } from "./ui";

it("renders a native button that passes every prop through and defaults to type=button", () => {
  const click = vi.fn();
  render(<form onSubmit={e => e.preventDefault()}><Button variant="primary" size="lg" aria-describedby="x" onClick={click} disabled={false}>Generate video</Button><Button variant="danger" type="submit">Delete</Button></form>);
  const button = screen.getByRole("button", { name: "Generate video" });
  expect(button.tagName).toBe("BUTTON");
  expect(button).toHaveAttribute("type", "button");
  expect(button).toHaveAttribute("aria-describedby", "x");
  expect(button).toHaveClass("btn", "btn-primary", "btn-lg");
  fireEvent.click(button);
  expect(click).toHaveBeenCalledOnce();
  expect(screen.getByRole("button", { name: "Delete" })).toHaveAttribute("type", "submit");
  expect(screen.getByRole("button", { name: "Delete" })).toHaveClass("btn-danger", "btn-md");
});

it("labels badges by state with a decorative dot", () => {
  render(<><Badge state="needs-attention" /><Badge state="ready">Saved</Badge></>);
  expect(screen.getByText("Needs attention")).toHaveClass("badge", "badge-needs-attention");
  expect(screen.getByText("Saved").querySelector(".badge-dot")).toHaveAttribute("aria-hidden", "true");
});

it("keeps the given role on a banner and colours only its lead word", () => {
  render(<Banner tone="warning" role="alert" lead="Attention">Caption reading is incomplete.</Banner>);
  const banner = screen.getByRole("alert");
  expect(banner).toHaveTextContent("AttentionCaption reading is incomplete.");
  expect(banner).toHaveClass("banner", "banner-warning");
  expect(banner.querySelector(".banner-lead")).toHaveTextContent("Attention");
  render(<Banner role="note">Plain note</Banner>);
  expect(screen.getByRole("note")).toHaveTextContent(/^Plain note$/);
});

it("wires a field's label and hint to its native control without replacing its props", () => {
  render(<Field label="Audio" hint="Reference audio can include speech."><select aria-describedby="extra" defaultValue="mix"><option value="mix">Mix</option></select></Field>);
  const select = screen.getByRole("combobox", { name: "Audio" });
  expect(select).toHaveValue("mix");
  expect(select.getAttribute("aria-describedby")).toMatch(/^extra .+-hint$/);
  expect(select).toHaveAccessibleDescription("Reference audio can include speech.");
  render(<Field label="Name"><input id="project-name" /></Field>);
  expect(screen.getByRole("textbox", { name: "Name" })).toHaveAttribute("id", "project-name");
});

it("renders panels and empty states as plain containers", () => {
  render(<Panel aria-label="Create video"><EmptyState title="No clips yet" action={<Button>Choose videos</Button>}>Add MP4 or MOV files.</EmptyState></Panel>);
  expect(screen.getByRole("region", { name: "Create video" })).toHaveClass("panel");
  expect(screen.getByText("No clips yet")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Choose videos" })).toBeInTheDocument();
});
