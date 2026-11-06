using System;
using System.Runtime.InteropServices;

// Windows PowerShell's .NET Framework FolderBrowserDialog uses the legacy
// tree picker. Use the Common Item Dialog directly, without extra dependencies.
#if NET5_0_OR_GREATER
[System.Runtime.Versioning.SupportedOSPlatform("windows")]
#endif
public static class TP3DFolderPicker
{
    public static string Pick(IntPtr owner, string initialFolder, string title)
    {
        IFileDialog dialog = (IFileDialog)new FileOpenDialog();
        IShellItem folder = null;
        IShellItem result = null;
        try
        {
            uint options;
            dialog.GetOptions(out options);
            // FOS_NOCHANGEDIR | FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM | FOS_PATHMUSTEXIST
            dialog.SetOptions(options | 0x8 | 0x20 | 0x40 | 0x800);
            dialog.SetTitle(title);
            if (!String.IsNullOrEmpty(initialFolder))
            {
                Guid iid = typeof(IShellItem).GUID;
                SHCreateItemFromParsingName(initialFolder, IntPtr.Zero, ref iid, out folder);
                dialog.SetFolder(folder);
            }

            int status = dialog.Show(owner);
            if (status == unchecked((int)0x800704C7)) // ERROR_CANCELLED
                return String.Empty;
            Marshal.ThrowExceptionForHR(status);
            dialog.GetResult(out result);
            IntPtr path;
            result.GetDisplayName(0x80058000, out path); // SIGDN_FILESYSPATH
            try
            {
                return Marshal.PtrToStringUni(path);
            }
            finally
            {
                Marshal.FreeCoTaskMem(path);
            }
        }
        finally
        {
            if (result != null) Marshal.ReleaseComObject(result);
            if (folder != null) Marshal.ReleaseComObject(folder);
            Marshal.ReleaseComObject(dialog);
        }
    }

    [DllImport("shell32.dll", CharSet = CharSet.Unicode, PreserveSig = false)]
    private static extern void SHCreateItemFromParsingName(
        string path, IntPtr bindContext, ref Guid iid, out IShellItem item);

    [ComImport, Guid("DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7")]
    private class FileOpenDialog { }

    // COM methods must remain in their native vtable order, including unused slots.
    [ComImport, Guid("42F85136-DB7E-439C-85F1-E4075D135FC8"),
     InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IFileDialog
    {
        [PreserveSig] int Show(IntPtr owner);
        void SetFileTypes(uint count, IntPtr filters);
        void SetFileTypeIndex(uint index);
        void GetFileTypeIndex(out uint index);
        void Advise(IntPtr events, out uint cookie);
        void Unadvise(uint cookie);
        void SetOptions(uint options);
        void GetOptions(out uint options);
        void SetDefaultFolder(IShellItem folder);
        void SetFolder(IShellItem folder);
        void GetFolder(out IShellItem folder);
        void GetCurrentSelection(out IShellItem item);
        void SetFileName([MarshalAs(UnmanagedType.LPWStr)] string name);
        void GetFileName(out IntPtr name);
        void SetTitle([MarshalAs(UnmanagedType.LPWStr)] string title);
        void SetOkButtonLabel([MarshalAs(UnmanagedType.LPWStr)] string text);
        void SetFileNameLabel([MarshalAs(UnmanagedType.LPWStr)] string label);
        void GetResult(out IShellItem item);
        void AddPlace(IShellItem item, uint placement);
        void SetDefaultExtension([MarshalAs(UnmanagedType.LPWStr)] string extension);
        void Close(int status);
        void SetClientGuid(ref Guid guid);
        void ClearClientData();
        void SetFilter(IntPtr filter);
    }

    [ComImport, Guid("43826D1E-E718-42EE-BC55-A1E261C37BFE"),
     InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IShellItem
    {
        void BindToHandler(IntPtr bindContext, ref Guid handler, ref Guid iid, out IntPtr result);
        void GetParent(out IShellItem parent);
        void GetDisplayName(uint kind, out IntPtr name);
        void GetAttributes(uint mask, out uint attributes);
        void Compare(IShellItem other, uint hint, out int order);
    }
}
