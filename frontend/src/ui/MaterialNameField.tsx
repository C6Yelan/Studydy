import { useId } from "react";
import "./material-name-field.css";

export function MaterialNameField({value,onChange,disabled=false}:{value:string;onChange:(name:string)=>void;disabled?:boolean}) {
  const errorId=useId();
  return <div className="material-name-field">
    <label><span>教材名稱</span><input aria-label="教材名稱" value={value} disabled={disabled} maxLength={180}
      aria-invalid={!value.trim()} aria-describedby={!value.trim()?errorId:undefined} onChange={event=>onChange(event.target.value)}/></label>
    {!value.trim() && <p id={errorId} className="form-error" role="alert">請填寫教材名稱。</p>}
  </div>;
}
