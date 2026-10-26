// Viewing rotation for a page. The page and every box drawn on it live in ONE wrapper that is turned as a whole, so
// boxes keep their original coordinates and always stay on the text they mark. These helpers only do the geometry
// for the places that need it: the frame size, the transform, scrolling to a box, and dragging a box while turned.

export const normalizeRotation = (deg) => (((Math.round((Number(deg) || 0) / 90) * 90) % 360) + 360) % 360;

// A point of the (unturned) page, W x H pixels as drawn, to its place in the turned frame. Rotation is clockwise.
export function rotatePoint(x, y, rot, W, H) {
  switch (normalizeRotation(rot)) {
    case 90: return { x: H - y, y: x };
    case 180: return { x: W - x, y: H - y };
    case 270: return { x: y, y: W - x };
    default: return { x, y };
  }
}

// Size of the frame that holds the turned page.
export function frameSize(rot, W, H) {
  return normalizeRotation(rot) % 180 === 0 ? { width: W, height: H } : { width: H, height: W };
}

// CSS for the inner (page + boxes) element: it is placed at the frame's top-left, then turned about that corner and
// moved back into the frame.
export function pageTransform(rot, W, H) {
  switch (normalizeRotation(rot)) {
    case 90: return `translateX(${H}px) rotate(90deg)`;
    case 180: return `translate(${W}px, ${H}px) rotate(180deg)`;
    case 270: return `translateY(${W}px) rotate(270deg)`;
    default: return 'none';
  }
}

// A mouse movement on screen, to the same movement in the page's own (unturned) directions.
export function localDelta(dx, dy, rot) {
  switch (normalizeRotation(rot)) {
    case 90: return { dx: dy, dy: -dx };
    case 180: return { dx: -dx, dy: -dy };
    case 270: return { dx: -dy, dy: dx };
    default: return { dx, dy };
  }
}

// Top edge, in the turned frame, of a box given in page pixels (scaleX/scaleY turn page pixels into drawn pixels).
export function boxTopInFrame(bbox, rot, scaleX, scaleY, W, H) {
  const xs = [bbox.x1, bbox.x2];
  const ys = [bbox.y1, bbox.y2];
  let top = Infinity;
  for (const x of xs) for (const y of ys) top = Math.min(top, rotatePoint(x * scaleX, y * scaleY, rot, W, H).y);
  return top;
}

const KEY = (docId) => `docint:rotation:${docId}`;

export function loadRotations(docId) {
  if (!docId) return {};
  try {
    const raw = window.localStorage.getItem(KEY(docId));
    const obj = raw ? JSON.parse(raw) : {};
    const out = {};
    Object.keys(obj || {}).forEach((p) => { const r = normalizeRotation(obj[p]); if (r) out[p] = r; });
    return out;
  } catch {
    return {};
  }
}

export function saveRotations(docId, map) {
  if (!docId) return;
  try {
    if (Object.keys(map).length === 0) window.localStorage.removeItem(KEY(docId));
    else window.localStorage.setItem(KEY(docId), JSON.stringify(map));
  } catch {
    /* storage can be blocked: rotation then lasts until the page is closed */
  }
}
