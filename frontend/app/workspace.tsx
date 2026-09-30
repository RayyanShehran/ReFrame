"use client";

import { useState } from "react";
import { ClipUpload } from "./clip-upload";
import { ReferenceSelection } from "./reference-selection";

export function Workspace() {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  return <>
    <ReferenceSelection onSelectedChange={setSelectedId} />
    {selectedId && <ClipUpload key={selectedId} />}
  </>;
}
