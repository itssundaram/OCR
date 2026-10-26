import { useCallback, useEffect, useState } from 'react';
import { loadRotations, normalizeRotation, saveRotations } from '../utils/rotation';

// Per-page viewing rotation, remembered per document in this browser.
export function usePageRotation(docId, page) {
  const [map, setMap] = useState(() => loadRotations(docId));
  useEffect(() => { setMap(loadRotations(docId)); }, [docId]);

  const turn = useCallback((delta) => {
    setMap((prev) => {
      const next = { ...prev };
      const r = normalizeRotation((prev[page] || 0) + delta);
      if (r) next[page] = r; else delete next[page];
      saveRotations(docId, next);
      return next;
    });
  }, [docId, page]);

  const rotation = map[page] || 0;
  return {
    rotation,
    rotationFor: (p) => map[p] || 0,
    rotateCW: useCallback(() => turn(90), [turn]),
    rotateCCW: useCallback(() => turn(-90), [turn]),
    resetRotation: useCallback(() => turn(-rotation), [turn, rotation]),
  };
}

export default usePageRotation;
