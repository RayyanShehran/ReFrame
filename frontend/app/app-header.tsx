"use client";

import type { ReactNode } from "react";
import { Connectivity } from "./connectivity";

/** The one site header (`header.topbar`): brand, project name, a menu, API status and actions. */
export function AppHeader({ project, menu, actions }: { project: ReactNode; menu?: ReactNode; actions?: ReactNode }) {
  return <header className="topbar">
    <span className="brand">ReFrame<span className="brand-dot">.</span></span>
    <span className="header-project">{project}</span>
    {menu && <div className="header-menu">{menu}</div>}
    <Connectivity />
    {actions && <div className="header-actions">{actions}</div>}
  </header>;
}
