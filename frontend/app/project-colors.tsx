"use client";

import { OptionalFeatures } from "./optional-features";
import { Preparation } from "./preparation";
import { WorkspaceSection } from "./guided-workspace";
import { PacingBlueprint } from "./pacing-blueprint";
import { useState } from "react";
import { ColorRecipe } from "./color-recipe";
import { ReferenceMedia } from "./reference-media";
import { StyleBlueprint, type Blueprint } from "./style-blueprint";

export function colorDifferences(reference: Blueprint["color"], footage: Blueprint["color"]) {
  return [reference.brightness_p50 - footage.brightness_p50,
    reference.contrast_spread - footage.contrast_spread,
    reference.mean_hsv_saturation - footage.mean_hsv_saturation,
    ...reference.rgb_mean.map((value, index) => value - footage.rgb_mean[index])].map(value => value * 100);
}

export function ProjectColors({ projectId, hasFootage, footageDuration = null, libraryKey = "" }: { projectId: string; hasFootage: boolean; footageDuration?: number | null; libraryKey?: string }) {
  const [mediaReady, setMediaReady] = useState(false);
  const [reference, setReference] = useState<Blueprint | null>(null);
  const [footage, setFootage] = useState<Blueprint | null>(null);
  const ready = reference && footage && hasFootage;
  const names = ["Median encoded brightness", "Contrast spread", "Mean HSV saturation", "Mean red", "Mean green", "Mean blue"];
  const values = (b: Blueprint) => [b.color.brightness_p50, b.color.contrast_spread, b.color.mean_hsv_saturation, ...b.color.rgb_mean];
  const differences = ready ? colorDifferences(reference.color, footage.color) : [];
  return <Preparation key={projectId} hasFootage={hasFootage}>
    <WorkspaceSection section="reference"><ReferenceMedia projectId={projectId} onReady={setMediaReady} showAnalysis={false} onColorChange={setReference} /></WorkspaceSection>
    <WorkspaceSection section="audio"><details><summary>Optional feature readiness</summary><OptionalFeatures key={projectId} projectId={projectId} /></details></WorkspaceSection>
    <ColorRecipe key={projectId} projectId={projectId} analysesReady={!!ready} sourceKey={`${hasFootage}-${mediaReady}-${libraryKey}`} footageDuration={footageDuration} />
    <WorkspaceSection section="style">
    {mediaReady && <StyleBlueprint projectId={projectId} onChange={setReference} />}
    {hasFootage && <StyleBlueprint projectId={projectId} footage onChange={setFootage} />}
    <section className="reference-section" aria-label="Reference and footage color comparison">
      <h3>Reference / Your footage</h3>
      {!reference && <p>Retrieve the reference and analyze its color successfully to compare.</p>}
      {!hasFootage && <p>Upload your footage, then select Analyze my footage.</p>}
      {hasFootage && !footage && <p>Analyze my footage successfully, or retry its failed analysis, to compare.</p>}
      {ready && <>
        <details><summary>Compare sampled colors</summary><div className="color-comparison"><table><thead><tr><th scope="col">Sampled measurement</th><th scope="col">Reference</th><th scope="col">Your footage</th><th scope="col">Reference − footage (percentage points)</th></tr></thead>
          <tbody><tr><th scope="row">Palette</th>{[reference, footage].map((b, index) => <td key={index}><ul className="palette">{b.color.palette.map(c => <li key={c.hex}><span className="palette-swatch" aria-hidden="true" style={{ backgroundColor: c.hex }} />{c.hex} · {(c.proportion * 100).toFixed(1)}%</li>)}</ul></td>)}<td>Color bins, no correction inferred</td></tr>
          {names.map((name, index) => <tr key={name}><th scope="row">{name}</th><td>{(values(reference)[index] * 100).toFixed(1)}%</td><td>{(values(footage)[index] * 100).toFixed(1)}%</td><td>{differences[index] > 0 ? "+" : ""}{differences[index].toFixed(1)} pp</td></tr>)}</tbody>
        </table></div></details>
        <p className="hint">Differences describe sampled pixels and can reflect different scene content. They are not exposure stops, white-balance corrections or a ready-to-apply grade.</p>
      </>}
    </section>
    {mediaReady && <PacingBlueprint projectId={projectId} />}
    </WorkspaceSection>
  </Preparation>;
}
