import { useEffect, useState } from "react";

import { LevelStep } from "../components/LevelStep";
import { ResumeStep } from "../components/ResumeStep";
import { ScoresStep } from "../components/ScoresStep";
import { SkillsStep } from "../components/SkillsStep";
import { getMe, type Me, type Resume } from "../api";

const STEP_LABELS = ["Resume", "Skills", "Level", "Scores"];

/** The four required steps, in order. Which one shows is decided by what the
 *  API says is done, so a reload never loses your place. */
export function Onboarding({ me: initial, onDone }: { me: Me; onDone: () => void }) {
  const [me, setMe] = useState(initial);
  const [resume, setResume] = useState<Resume | null>(null);
  const { onboarding } = me;

  const step = !onboarding.resume ? 0 : !onboarding.skills_confirmed ? 1 : !onboarding.level ? 2 : 3;

  useEffect(() => {
    if (onboarding.scores_confirmed) onDone();
  }, [onboarding.scores_confirmed, onDone]);

  const refresh = () => getMe().then(setMe);

  return (
    <div className="centered">
      <div className="card fadein">
        <ol className="wizard">
          {STEP_LABELS.map((label, index) => (
            <li key={label} className={index === step ? "current" : index < step ? "done" : ""}>
              <span className="wizard-num">{index < step ? "✓" : index + 1}</span>
              {label}
            </li>
          ))}
        </ol>
        {step === 0 && <ResumeStep onUploaded={(uploaded) => { setResume(uploaded); return refresh(); }} />}
        {step === 1 && <SkillsStep resume={resume} onConfirmed={refresh} />}
        {step === 2 && <LevelStep onPicked={refresh} />}
        {step === 3 && <ScoresStep onConfirmed={refresh} />}
      </div>
    </div>
  );
}
