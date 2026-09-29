# Architecture

## Implemented foundation

The Next.js frontend presents the application shell and checks the FastAPI `/health` endpoint in the browser. The FastAPI backend reports API availability only. It has no media processing or storage behavior.

## Planned processing flow

Reference analysis → Style Blueprint → user settings → Edit Plan → rendering.

Reference analysis will produce **observations** about source media. The Style Blueprint will hold those observations as structured, editable data. User settings will express **overrides** and selections. An Edit Plan will turn the selected style and user settings into **executable operations**. Rendering will execute that plan against the user's media. Keeping these boundaries separate prevents analysis data from becoming rendering instructions by accident.

Future user media should live under ignored runtime storage, outside `frontend/public`. A future worker boundary should handle long-running media work independently of API request handling. Neither storage nor workers are implemented in this milestone.
