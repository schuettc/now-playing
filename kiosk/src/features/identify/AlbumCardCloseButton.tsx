interface Props {
  onClose: () => void;
}

export function AlbumCardCloseButton({ onClose }: Props) {
  return (
    <button
      type="button"
      aria-label="Close tracklist"
      onClick={(ev) => {
        ev.stopPropagation();
        onClose();
      }}
      style={{ touchAction: 'manipulation' }}
      className="absolute right-3 top-3 z-10 flex h-11 w-11 cursor-pointer items-center justify-center rounded-full border border-[#1f1f25] bg-black/40 text-2xl leading-none text-[#e9e9ee] hover:border-[#6e8aff] hover:text-[#6e8aff]"
    >
      ×
    </button>
  );
}
