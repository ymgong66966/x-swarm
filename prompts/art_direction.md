# Art direction for generated images

One recognisable look across every post, so the timeline reads as one account. Edit this
file to change the house style — it is the only place the look is defined, and both the
Illustrator's prompt and the final image prompt are assembled from it.

## House style (always applied)

Dark editorial tech illustration on near-black `#0d1117`. One dominant accent of
electric blue `#58a6ff`, one secondary of warm orange `#f78166`, everything else in
cool greys. Flat vector-adjacent rendering with subtle depth: soft glow along accent
edges, fine grid or contour lines in the background at low opacity, generous negative
space. Precise and engineered, never cute, never corporate-stock, no people's faces,
no logos, no glossy 3D chrome, no lens flare, no clip art.

Composition: a single clear subject, off-centre, roughly 3:2 landscape, with the left
third kept quiet so the post's text preview does not fight the focal point.

**Hard constraint: no words, numbers, letters, or UI text anywhere in the image.** Real
figures belong in the deterministic chart templates, where they cannot be hallucinated;
generated art carries the *idea*, not the data.

## Named styles

### frontier_diagram
An abstract systems diagram: nodes, layered stacks, or signal paths, drawn as if lifted
from a research paper figure and redrawn by a designer. Use for architectures,
pipelines, training/inference mechanics — anything where the point is *how it works*.

### risk_dark
Higher contrast, more negative space, one fragile or fracturing element in orange
against blue order: a cracked lattice, a diverging path, an eroding surface. Use for
regressions, safety and failure modes, hidden costs, "this scales badly" arguments.

### data_poster
Chart-adjacent abstraction: rhythmic bars, ascending curves, or distribution shapes with
no axis labels, treated as poster geometry rather than a real plot. Use when the post is
about a measured jump or gap and the number itself lives in the text.

### clinical_calm
Softer version of the house style for care-stream posts: more negative space, warmer
greys, the blue accent used sparingly. Objects and environments only — a doorway with a
grab rail, a walker beside a chair, a tablet on a kitchen table — drawn with dignity and
never with faces or medical drama. Use for caregiving, telehealth and policy posts.

### site_hero
The one light style, for headers of articles that ship on the Alverna site rather than
into the timeline — a dark image inside that warm cream page would read as a hole. Warm
off-white ground `#fbf9f5`, deep ink `#1b2430` linework, a calm teal accent `#1e7d8c`
with one soft coral `#e88a6a` note, no glow. Same restraint as `clinical_calm`: objects
and environments only, no faces, no medical drama, wide margins, roughly 3:1 landscape
because the site crops it into a banner. Avoid subjects whose realism demands legible
type — screens showing an interface, open documents, forms, signage: draw them blank,
closed, or seen edge-on, or pick a different object entirely.

### site_photo
The style for Alverna article banners, and the only photographic one. A documentary
editorial photograph of real caregiving: a daughter steadying her father's forearm, a
nurse kneeling to show a transfer grip, a home-health visit at a kitchen table, a
video visit taken on a tablet propped against a fruit bowl. Real people of the ages the
article is actually about, caught mid-action rather than posing — hands, posture and
attention carry the meaning. Soft daylight from a window, ordinary lived-in domestic
interiors, shallow depth of field, 35mm, natural skin and fabric texture, warm muted
palette that sits calmly next to the site's cream page. Dignified and matter-of-fact:
no medical drama, no stock-photo cheerfulness, no cold blue-grey hospital cast. Roughly
3:2 landscape with one side kept quiet, because the site crops it into a banner.

### concept_hero
A single strong metaphor object rendered with care — a lens, a bridge, a key, a valve,
a scaffold — floating in dark space with a soft accent rim. Use for opinion posts,
essays, and anything announcing your own work.
