'use strict';

const USAGE = 'Usage: node multiplication.js <a> <b>';

/**
 * コマンドライン引数から2つの数値を受け取り、積を出力する
 */
function main() {
  const args = process.argv.slice(2);

  if (args.length !== 2) {
    process.stderr.write(`Error: 引数を2つ指定してください。\n${USAGE}\n`);
    process.exit(1);
  }

  const [rawA, rawB] = args;
  const a = Number(rawA);
  const b = Number(rawB);

  const isValidNumber = (value, raw) => !isNaN(value) && raw.trim() !== '';

  if (!isValidNumber(a, rawA)) {
    process.stderr.write(`Error: 第1引数 "${rawA}" は数値ではありません。\n`);
    process.exit(1);
  }

  if (!isValidNumber(b, rawB)) {
    process.stderr.write(`Error: 第2引数 "${rawB}" は数値ではありません。\n`);
    process.exit(1);
  }

  const result = a * b;
  process.stdout.write(`${a} * ${b} = ${result}\n`);
  process.exit(0);
}

main();
