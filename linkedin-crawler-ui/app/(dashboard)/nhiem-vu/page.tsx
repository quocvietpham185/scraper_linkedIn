import { Metadata } from "next";
import { EmployeeSeedingTasksContent } from "@/components/features/dashboard/EmployeeSeedingTasksContent";

export const metadata: Metadata = {
  title: "Nhiem vu seeding | LinkedIn Scraper",
  description: "Trang nhan vien xu ly va xac minh nhiem vu seeding",
};

export default function KpiTasksPage() {
  return (
    <div className="flex h-full flex-col p-6">
      <EmployeeSeedingTasksContent />
    </div>
  );
}
