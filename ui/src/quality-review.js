// Human labels are explicit and remain separate from automated selection scores.
function qualityRatingRows() {
  return generatedClips.map(clip => ({source:selectedVideo || clip.source_file, start:clip.start_time, end:clip.end_time, title:clip.title,
    rating:clip.quality_review?.rating || 'unrated', boundaries:clip.quality_review?.boundaries || 'unrated',
    title_accuracy:clip.quality_review?.title || 'unrated', reviewed_at:clip.quality_review?.reviewed_at || null}));
}
function exportQualityRatings() {
  const rows = qualityRatingRows();
  if (!rows.length) { showToast('Generate or open clips before exporting ratings.', 'info'); return; }
  const blob = new Blob([JSON.stringify(rows, null, 2)], {type:'application/json'});
  const url = URL.createObjectURL(blob), link = document.createElement('a');
  link.href = url; link.download = 'klipzy-quality-ratings.json'; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
document.addEventListener('DOMContentLoaded', () => {
  const density = document.getElementById('clip-density');
  if (density) {
    density.value = localStorage.getItem('klipzy.clipDensity') === 'compact' ? 'compact' : 'comfortable';
    const apply = () => { document.getElementById('clips-grid')?.classList.toggle('compact', density.value === 'compact'); localStorage.setItem('klipzy.clipDensity', density.value); };
    density.addEventListener('change', apply); apply();
  }
  document.getElementById('export-quality-ratings')?.addEventListener('click', exportQualityRatings);
});
