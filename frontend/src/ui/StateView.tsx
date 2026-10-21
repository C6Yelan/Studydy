import { Icon, type IconName } from "./Icon";
import { PageSkeleton, type SkeletonVariant } from "./Skeleton";
import "./styles.css";

type StateTone = "loading" | "empty" | "failure";

export function StateView({
  action,
  description,
  icon = "warning",
  image,
  live = false,
  skeleton = "block",
  title,
  tone,
}: {
  action?: React.ReactNode;
  description: string;
  icon?: IconName;
  image?: string;
  live?: boolean;
  /** 讀取中顯示的骨架版型；頁面層級的讀取指定對應版型，區塊內預設為幾行佔位。 */
  skeleton?: SkeletonVariant;
  title: string;
  tone: StateTone;
}) {
  if (tone === "loading") return <PageSkeleton variant={skeleton} label={title} live={live} />;
  const isFailure = tone === "failure";
  return (
    <section
      aria-live={live && !isFailure ? "polite" : undefined}
      className={`state-view state-view--page is-${tone}`}
      role={isFailure ? "alert" : undefined}
    >
      {image ? (
        <img className="state-view__image" src={image} alt="" />
      ) : (
        <span className="state-view__icon">
          <Icon name={icon} size={26} />
        </span>
      )}
      <h1>{title}</h1>
      <p>{description}</p>
      {action && <div className="state-view__actions">{action}</div>}
    </section>
  );
}
