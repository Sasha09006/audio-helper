import { useState } from "react";
import CitySelect from "./components/CitySelect.jsx";
import Recorder from "./components/Recorder.jsx";

export default function App() {
  const [city, setCity] = useState("杭州");

  return (
    <main className="page">
      <h1>语音约碰面地点</h1>
      <p>按住录音，说出两人所在地点和想找的店。当前仅支持同城两人。</p>
      <CitySelect value={city} onChange={setCity} />
      <Recorder />
      <p className="hint">本轮只做本地录音，不会识别文字，也不会查询店铺。</p>
    </main>
  );
}
