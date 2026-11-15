import {AbsoluteFill, Audio, Sequence, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {createTikTokStyleCaptions, Caption} from '@remotion/captions';
import timeline from '../public/voiceover/voiceover.json';
import captions from '../public/voiceover/captions.json';

export const Narrated: React.FC = () => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const {pages} = createTikTokStyleCaptions({captions: captions as Caption[], combineTokensWithinMilliseconds: 1200});
  const ms = (frame / fps) * 1000;
  const page = pages.find((p) => ms >= p.startMs && ms < p.startMs + p.durationMs);
  return (
    <AbsoluteFill>
      {timeline.lines.map((l) => (
        <Sequence key={l.id} from={l.startFrame} durationInFrames={l.durationInFrames}>
          <Audio src={staticFile(l.src)} />
        </Sequence>
      ))}
      {page && <AbsoluteFill style={{justifyContent: 'flex-end', alignItems: 'center', paddingBottom: 160}}>
        <div style={{fontSize: 64, fontWeight: 800, color: 'white', textShadow: '0 4px 12px #000'}}>{page.text}</div>
      </AbsoluteFill>}
    </AbsoluteFill>
  );
};
