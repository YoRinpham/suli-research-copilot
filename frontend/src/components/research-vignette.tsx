import Image from "next/image";

/** Approved line drawing with a continuous research path and unfolding paper. */
export function ResearchVignette() {
  return <Image
    className="research-vignette"
    src="/images/paper-landscape-lineart.png"
    alt=""
    width={1536}
    height={1024}
    sizes="(max-width: 1100px) 40vw, 480px"
    unoptimized
    preload
  />;
}
