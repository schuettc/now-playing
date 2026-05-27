import { motion } from 'framer-motion';
import { useDominantColor } from '@/hooks/useDominantColor';

interface Props {
  src?: string;
  identity: string;
  // When true (paused stream / airplay), reduce the backdrop's
  // visibility ~40% so the screen reads as "not actively playing"
  // without going full-dark.
  dim?: boolean;
}

/** Static gradient shown when there is no art URL. */
function StaticBackdrop() {
  return (
    <div className="absolute inset-0 bg-gradient-to-br from-zinc-900 via-black to-zinc-950" />
  );
}

/** Blurred + tinted art backdrop shown when art is available. */
function BackdropBlurLayer({
  src,
  tint,
}: {
  src: string;
  tint: string | null;
}) {
  return (
    <>
      <div
        className="absolute inset-0 scale-125"
        style={{
          backgroundImage: `url("${src}")`,
          backgroundSize: 'cover',
          backgroundPosition: 'center',
          filter: 'blur(80px) saturate(1.3) brightness(0.55)',
          transform: 'scale(1.25)',
        }}
      />
      {tint && (
        <div
          className="absolute inset-0"
          style={{
            background: `radial-gradient(ellipse at 50% 35%, ${tint} 0%, transparent 70%)`,
            opacity: 0.55,
            mixBlendMode: 'screen',
          }}
        />
      )}
      <div className="absolute inset-0 bg-black/30" />
    </>
  );
}

export function BlurredBackdrop({ src, identity, dim = false }: Props) {
  const color = useDominantColor(src);
  const tint = color ? `rgb(${color.r} ${color.g} ${color.b})` : null;

  if (!src) return <StaticBackdrop />;

  return (
    <motion.div
      key={identity}
      initial={{ opacity: 0 }}
      animate={{ opacity: dim ? 0.35 : 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.5, ease: 'easeOut' }}
      style={{ willChange: 'opacity, transform' }}
      className="absolute inset-0"
    >
      <BackdropBlurLayer src={src} tint={tint} />
    </motion.div>
  );
}
