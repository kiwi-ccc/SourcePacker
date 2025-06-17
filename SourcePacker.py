#!/usr/bin/env python
# -*- coding: utf-8 -*-

import argparse
import base64
import os
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

# --- 定数定義 ---
BOUNDARY = "[--_SOURCE_PACKER_FILE_BOUNDARY_--]"
DEFAULT_EXCLUDES = {
    'dirs': {'__pycache__', '.git', '.vs', 'x64', 'Debug', 'Release', 'ipch'},
    'files': {'.suo', '.user'},
    'exts': {'.obj', '.ilk', '.pdb', '.tlog', '.pch', '.res', '.exe', '.dll', '.lib', '.ncb', '.sdf'}
}

# --- .sln/.vcxproj 解析ロジック ---

def parse_sln(sln_path):
    """ .slnファイルを解析し、含まれる.vcxprojファイルのパスリストを返す """
    sln_dir = sln_path.parent
    project_files = []
    # Regex to find project lines like: Project("{GUID}") = "Name", "Path\To\Project.vcxproj", "{GUID}"
    project_regex = re.compile(r'Project\("\{[A-F0-9-]+\}"\) = ".*?", "(.*\.vcxproj)", ".*?"', re.IGNORECASE)
    
    try:
        with open(sln_path, 'r', encoding='utf-8-sig') as f:
            for line in f:
                match = project_regex.search(line)
                if match:
                    # Resolve the path relative to the .sln file's directory
                    proj_path = sln_dir / Path(match.group(1).strip())
                    if proj_path.is_file():
                        project_files.append(proj_path.resolve())
    except Exception as e:
        print(f"Error parsing .sln file: {e}", file=sys.stderr)
    return project_files

def parse_vcxproj(vcxproj_path):
    """ .vcxprojファイルを解析し、含まれるファイルの相対パスリストを返す """
    vcxproj_dir = vcxproj_path.parent
    included_files = set()
    
    try:
        tree = ET.parse(vcxproj_path)
        root = tree.getroot()
        # MSBuild XMLs have a namespace, which we need to handle
        ns = {'ms': 'http://schemas.microsoft.com/developer/msbuild/2003'}
        
        item_tags = ['ClCompile', 'ClInclude', 'ResourceCompile', 'None', 'Image', 'CustomBuild', 'FxCompile']
        
        for tag in item_tags:
            for item in root.findall(f'.//ms:{tag}', ns):
                if 'Include' in item.attrib:
                    # The path is relative to the .vcxproj file
                    file_rel_path = Path(item.attrib['Include'])
                    # We need the full path to check if it exists before adding
                    full_path = vcxproj_dir / file_rel_path
                    if full_path.is_file():
                        included_files.add(full_path.resolve())
                        
    except Exception as e:
        print(f"Warning: Could not parse {vcxproj_path.name}: {e}", file=sys.stderr)
        
    return list(included_files)

# --- コア機能 ---

def pack(target_path, output_path=None):
    """ 指定された対象をパックする """
    target_path = Path(target_path).resolve()
    
    if not target_path.exists():
        print(f"Error: Target path '{target_path}' does not exist.", file=sys.stderr)
        return

    # --- 出力パスの決定 ---
    if output_path:
        output_path = Path(output_path).resolve()
    else:
        # If target is a file (sln/vcxproj), use its stem. If a dir, use its name.
        base_name = target_path.stem if target_path.is_file() else target_path.name
        output_path = Path.cwd() / f"{base_name}.spack"

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # --- パック対象ファイルリストの構築 ---
    files_to_pack = set()
    base_dir = None

    if target_path.is_file() and target_path.suffix.lower() in ['.sln', '.vcxproj']:
        base_dir = target_path.parent
        files_to_pack.add(target_path) # ソリューション/プロジェクトファイル自体を追加
        
        project_files = []
        if target_path.suffix.lower() == '.sln':
            project_files = parse_sln(target_path)
        else: # .vcxproj
            project_files = [target_path]
            
        for proj_path in project_files:
            files_to_pack.add(proj_path)
            # .vcxproj.filters も追加 (IDEでの表示に必要)
            filters_path = proj_path.with_suffix('.vcxproj.filters')
            if filters_path.is_file():
                files_to_pack.add(filters_path)
            
            # 各プロジェクトからファイルリストを取得
            for f in parse_vcxproj(proj_path):
                files_to_pack.add(f)
        print(f"Intelligent packing based on '{target_path.name}'.")

    elif target_path.is_dir():
        base_dir = target_path
        print(f"Folder-based packing for '{target_path.name}'.")
        for root, dirs, files in os.walk(target_path):
            # 除外ディレクトリのフィルタリング
            dirs[:] = [d for d in dirs if d.lower() not in DEFAULT_EXCLUDES['dirs']]
            
            for file in files:
                file_path = Path(root) / file
                if file.lower() not in DEFAULT_EXCLUDES['files'] and file_path.suffix.lower() not in DEFAULT_EXCLUDES['exts']:
                    files_to_pack.add(file_path.resolve())
    
    else:
        print(f"Error: Unsupported target type '{target_path.name}'. Please specify a .sln, .vcxproj, or a directory.", file=sys.stderr)
        return

    if not files_to_pack:
        print("No files found to pack.", file=sys.stderr)
        return

    # --- パック処理の実行 ---
    print(f"Packing {len(files_to_pack)} files into '{output_path}'...")
    try:
        with open(output_path, 'w', encoding='ascii') as f_out:
            for file_path in sorted(list(files_to_pack)):
                try:
                    relative_path = file_path.relative_to(base_dir).as_posix() # POSIX形式でパスを統一
                    
                    with open(file_path, 'rb') as f_in:
                        content_binary = f_in.read()
                    
                    content_base64 = base64.b64encode(content_binary).decode('ascii')
                    
                    f_out.write(f"{BOUNDARY}\n")
                    f_out.write(f"Path: {relative_path}\n")
                    f_out.write(f"Size: {len(content_binary)}\n")
                    f_out.write("Encoding: Base64\n\n")
                    f_out.write(content_base64)
                    f_out.write("\n")

                except Exception as e:
                    print(f"Warning: Could not pack file {file_path}. Reason: {e}", file=sys.stderr)

        print("Packing completed successfully.")
        print(f"Total files: {len(files_to_pack)}")
        print(f"Output file: {output_path}")

    except Exception as e:
        print(f"An error occurred during packing: {e}", file=sys.stderr)


def unpack(archive_path, dest_dir):
    """ .spack ファイルを復元する """
    archive_path = Path(archive_path).resolve()
    dest_dir = Path(dest_dir).resolve()

    if not archive_path.is_file():
        print(f"Error: Archive file '{archive_path}' not found.", file=sys.stderr)
        return

    # 出力先を <dest_dir>/<project_name> にする
    project_name = archive_path.stem
    output_root = dest_dir / project_name
    output_root.mkdir(parents=True, exist_ok=True)
    
    print(f"Unpacking '{archive_path.name}' into '{output_root}'...")
    
    try:
        with open(archive_path, 'r', encoding='ascii') as f:
            content = f.read()
        
        # Split by boundary, filter out empty parts
        file_blocks = [block for block in content.split(BOUNDARY) if block.strip()]
        
        for block in file_blocks:
            try:
                header_part, data_part = block.strip().split('\n\n', 1)
                headers = {}
                for line in header_part.split('\n'):
                    if ':' in line:
                        key, value = line.split(':', 1)
                        headers[key.strip()] = value.strip()
                
                if 'Path' not in headers:
                    print("Warning: Found a block without a Path header. Skipping.", file=sys.stderr)
                    continue

                relative_path = Path(headers['Path'])
                output_file_path = output_root / relative_path
                
                # Ensure parent directory exists
                output_file_path.parent.mkdir(parents=True, exist_ok=True)

                # Decode and write
                if headers.get('Encoding') == 'Base64':
                    file_data = base64.b64decode(data_part.strip())
                    with open(output_file_path, 'wb') as f_out:
                        f_out.write(file_data)
                else:
                    # For potential future encodings, though we only use Base64 now
                    with open(output_file_path, 'w', encoding='utf-8') as f_out:
                        f_out.write(data_part)

            except Exception as e:
                print(f"Warning: Failed to unpack a file block. Reason: {e}", file=sys.stderr)
        
        print("Unpacking completed successfully.")
        print(f"Total files restored: {len(file_blocks)}")
        print(f"Output directory: {output_root}")

    except Exception as e:
        print(f"An error occurred during unpacking: {e}", file=sys.stderr)


# --- メイン処理 ---
def main():
    parser = argparse.ArgumentParser(
        description="A tool to pack a Visual Studio project into a single text file and unpack it.",
        epilog="Example: SourcePacker.py pack C:\\dev\\MyProject\\MySolution.sln"
    )
    subparsers = parser.add_subparsers(dest='command', required=True)

    # Pack command
    parser_pack = subparsers.add_parser('pack', help='Pack a project folder into a single file.')
    parser_pack.add_argument('target', help='Path to the .sln, .vcxproj file, or project directory.')
    parser_pack.add_argument('output', nargs='?', help='(Optional) Path for the output .spack file.')
    parser_pack.set_defaults(func=lambda args: pack(args.target, args.output))

    # Unpack command
    parser_unpack = subparsers.add_parser('unpack', help='Unpack an archive file to a directory.')
    parser_unpack.add_argument('archive', help='Path to the .spack archive file.')
    parser_unpack.add_argument('destination', help='Path to the base directory where the project will be restored.')
    parser_unpack.set_defaults(func=lambda args: unpack(args.archive, args.destination))

    if len(sys.argv) == 1:
        parser.print_help(sys.stderr)
        sys.exit(1)
        
    args = parser.parse_args()
    args.func(args)

if __name__ == '__main__':
    main()