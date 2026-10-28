// One glyph per layer, because twenty-eight coloured dots are twenty-eight of the same dot.
//
// These are drawn, not fetched: an icon font from a CDN would be one more thing to be down, and
// the whole set inlined here costs less than a single request for it would. Each entry is the
// inside of a 24×24 <svg>, stroked in the layer's own colour — so the colour still groups
// (hazards warm, air and space blue, industry green) and the shape tells them apart.
//
// The shapes are in the Feather/Lucide idiom: 2px stroke, round caps, no fill.

window.LAYER_ICONS = {
  // ── our own collection ──────────────────────────────────────────────────────
  news: '<path d="M4 22h16a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2H8a2 2 0 0 0-2 2v16a2 2 0 0 1-2 2Z"/>' +
        '<path d="M4 22a2 2 0 0 1-2-2v-9c0-1.1.9-2 2-2h2"/><path d="M18 14h-8"/><path d="M15 18h-5"/>',
  protests: '<path d="m3 11 18-5v12L3 14v-3z"/><path d="M11.6 16.8a3 3 0 1 1-5.8-1.6"/>',
  conflict: '<path d="M14.5 17.5 3 6V3h3l11.5 11.5"/><path d="M13 19l6-6"/>' +
            '<path d="M16 16l4 4"/><path d="M19 21l2-2"/><path d="M9.5 17.5 21 6V3h-3L6.5 14.5"/>',

  // ── hazards ─────────────────────────────────────────────────────────────────
  quakes: '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
  gdacs: '<path d="m21.7 18-8-14a2 2 0 0 0-3.4 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.7-3Z"/>' +
         '<path d="M12 9v4"/><path d="M12 17h.01"/>',
  eonet: '<path d="M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.4-.5-2-1-3-1.1-2.1-.2-4 2-6 .5 2.5 2 4.9 4 6.5' +
         ' 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.2.4-2.3 1-3a2.5 2.5 0 0 0 2.5 2.5z"/>',
  outbreaks: '<circle cx="12" cy="12" r="4"/><path d="M12 2v4"/><path d="M12 18v4"/>' +
             '<path d="M2 12h4"/><path d="M18 12h4"/><path d="m4.9 4.9 2.9 2.9"/>' +
             '<path d="m16.2 16.2 2.9 2.9"/><path d="m19.1 4.9-2.9 2.9"/><path d="m7.8 16.2-2.9 2.9"/>',

  // ── air and space ───────────────────────────────────────────────────────────
  milair: '<path d="M17.8 19.2 16 11l3.5-3.5C21 6 21.5 4 21 3c-1-.5-3 0-4.5 1.5L13 8 4.8 6.2' +
          'c-.5-.1-.9.1-1.1.5l-.3.5c-.2.5-.1 1 .3 1.3L9 12l-2 3H4l-1 1 3 2 2 3 1-1v-3l3-2 3.5 5.3' +
          'c.3.4.8.5 1.3.3l.5-.2c.4-.3.6-.7.5-1.2z"/>',
  // A circle, not another warning triangle: next to "Disaster alerts" in the panel the two
  // triangles were the same glyph twice, which is the whole problem these icons exist to fix.
  airemerg: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5"/><path d="M12 16h.01"/>' +
            '<path d="M3.5 5.5 20 19"/>',
  sats: '<path d="M13 7 9 3 5 7l4 4"/><path d="m17 11 4 4-4 4-4-4"/>' +
        '<path d="m8 12 4 4 6-6-4-4Z"/><path d="m16 8 3-3"/><path d="M9 21a6 6 0 0 0-6-6"/>',
  spaceports: '<path d="M4.5 16.5c-1.5 1.3-2 5-2 5s3.7-.5 5-2c.7-.8.7-2.1-.1-2.9a2.2 2.2 0 0 0-2.9 0z"/>' +
              '<path d="m12 15-3-3a22 22 0 0 1 2-3.95A12.9 12.9 0 0 1 22 2c0 2.7-.8 7.5-6 11' +
              'a22.4 22.4 0 0 1-4 2z"/><path d="M9 12H4s.5-3.3 2-4c1.7-.8 5 0 5 0"/>' +
              '<path d="M12 15v5s3.3-.5 4-2c.8-1.7 0-5 0-5"/>',

  // ── infrastructure ──────────────────────────────────────────────────────────
  cables: '<path d="M2 6c.6.5 1.2 1 2.5 1C7 7 7 5 9.5 5s2.5 2 5 2 2.5-2 5-2c1.3 0 1.9.5 2.5 1"/>' +
          '<path d="M2 12c.6.5 1.2 1 2.5 1C7 13 7 11 9.5 11s2.5 2 5 2 2.5-2 5-2c1.3 0 1.9.5 2.5 1"/>' +
          '<path d="M2 18c.6.5 1.2 1 2.5 1C7 19 7 17 9.5 17s2.5 2 5 2 2.5-2 5-2c1.3 0 1.9.5 2.5 1"/>',
  outages: '<path d="m2 2 20 20"/><path d="M8.5 16.4a5 5 0 0 1 6-.8"/>' +
           '<path d="M5 12.9a10 10 0 0 1 5.2-2.7"/><path d="M19 12.9a10 10 0 0 0-2-1.5"/>' +
           '<path d="M2 8.8a15 15 0 0 1 4.2-2.6"/><path d="M22 8.8a15 15 0 0 0-8.3-4.2"/>' +
           '<path d="M12 20h.01"/>',
  datacenters: '<rect width="20" height="8" x="2" y="2" rx="2"/>' +
               '<rect width="20" height="8" x="2" y="14" rx="2"/>' +
               '<path d="M6 6h.01"/><path d="M6 18h.01"/>',
  ports: '<path d="M12 22V8"/><path d="M5 12H2a10 10 0 0 0 20 0h-3"/><circle cx="12" cy="5" r="3"/>',
  chokepoints: '<path d="M8 2v6l-4 4 4 4v6"/><path d="M16 2v6l4 4-4 4v6"/>',

  // ── energy and industry ─────────────────────────────────────────────────────
  nuclear: '<circle cx="12" cy="12" r="2"/><ellipse cx="12" cy="12" rx="10" ry="4"/>' +
           '<ellipse cx="12" cy="12" rx="10" ry="4" transform="rotate(60 12 12)"/>' +
           '<ellipse cx="12" cy="12" rx="10" ry="4" transform="rotate(-60 12 12)"/>',
  renewables: '<path d="M4 14a1 1 0 0 1-.8-1.6l9.9-10.2a.5.5 0 0 1 .9.5l-1.9 6a1 1 0 0 0 .9 1.3h7' +
              'a1 1 0 0 1 .8 1.6l-9.9 10.2a.5.5 0 0 1-.9-.5l1.9-6a1 1 0 0 0-.9-1.3z"/>',
  mines: '<path d="M14.5 12.5 6.6 20.4a1 1 0 1 1-3-3l7.9-7.9"/>' +
         '<path d="M18 9 15 6"/><path d="M14 11a6 6 0 0 1-6-6c3 0 5 1 7 3s3 4 3 7a6 6 0 0 1-4-4Z"/>',
  refineries: '<path d="M3 19a1 1 0 0 0 1 1h16a1 1 0 0 0 1-1V9l-6 4V9l-6 4V4a1 1 0 0 0-1-1H4' +
              'a1 1 0 0 0-1 1Z"/><path d="M8 16h.01"/><path d="M12 16h.01"/><path d="M16 16h.01"/>',
  bases: '<path d="M20 13c0 5-3.5 7.5-7.7 9a1 1 0 0 1-.7 0C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1' +
         'c2 0 4.5-1.2 6.2-2.7a1.2 1.2 0 0 1 1.5 0C14.5 3.8 17 5 19 5a1 1 0 0 1 1 1z"/>' +
         '<path d="m9 12 2 2 4-4"/>',

  // ── money and people ────────────────────────────────────────────────────────
  exchanges: '<path d="M16 7h6v6"/><path d="m22 7-8.5 8.5-5-5L2 17"/>',
  banks: '<path d="M11.1 2.2a2 2 0 0 1 1.8 0l7.9 3.8c.5.3.3 1-.2 1H3.5c-.5 0-.7-.7-.2-1z"/>' +
         '<path d="M6 18v-7"/><path d="M10 18v-7"/><path d="M14 18v-7"/><path d="M18 18v-7"/>' +
         '<path d="M3 22h18"/>',
  happiness: '<circle cx="12" cy="12" r="10"/><path d="M8 14s1.5 2 4 2 4-2 4-2"/>' +
             '<path d="M9 9h.01"/><path d="M15 9h.01"/>',

  // ── the surface itself ──────────────────────────────────────────────────────
  clouds: '<path d="M17.5 19H9a7 7 0 1 1 6.7-9h1.8a4.5 4.5 0 1 1 0 9Z"/>',
  rain: '<path d="M4 14.9A7 7 0 1 1 15.7 8h1.8a4.5 4.5 0 0 1 2.5 8.2"/>' +
        '<path d="M16 14v6"/><path d="M8 14v6"/><path d="M12 16v6"/>',
  wind: '<path d="M12.8 19.6A2 2 0 1 0 14 16H2"/><path d="M17.5 8a2.5 2.5 0 1 1 2 4H2"/>' +
        '<path d="M9.8 4.4A2 2 0 1 1 11 8H2"/>',
  sun: '<circle cx="12" cy="12" r="10"/><path d="M12 2v20"/><path d="M12 2a10 10 0 0 1 0 20z" ' +
       'fill="currentColor" stroke="none"/>',
};
