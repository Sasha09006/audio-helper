const CITIES = [
  "杭州",
  "北京",
  "上海",
  "广州",
  "深圳",
  "成都",
  "南京",
  "武汉",
  "西安",
  "苏州",
  "宁波",
  "青岛",
];

export default function CitySelect({ value, onChange }) {
  return (
    <label className="city-select">
      <span>所在城市</span>
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        {CITIES.map((city) => (
          <option key={city} value={city}>
            {city}
          </option>
        ))}
      </select>
    </label>
  );
}
