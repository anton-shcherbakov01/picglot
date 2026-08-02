/**
 * Legal and informational page copy.
 *
 * These are **templates**. They describe how the service actually behaves, which
 * is the hard part, but they have not been reviewed by a lawyer and are not
 * legal advice — see `docs/product/legal-review.md` before going live in any
 * particular jurisdiction.
 */

export interface ContentPage {
  slug: string;
  title: { en: string; ru: string };
  updated: string;
  template: boolean;
  sections: {
    heading: { en: string; ru: string };
    body: { en: string[]; ru: string[] };
  }[];
}

const section = (
  headingEn: string,
  headingRu: string,
  bodyEn: string[],
  bodyRu: string[],
) => ({ heading: { en: headingEn, ru: headingRu }, body: { en: bodyEn, ru: bodyRu } });

export const CONTENT_PAGES: ContentPage[] = [
  {
    slug: 'legal/privacy',
    title: { en: 'Privacy Policy', ru: 'Политика конфиденциальности' },
    updated: '2026-08-02',
    template: true,
    sections: [
      section(
        'What we store',
        'Что мы храним',
        [
          'The files you upload, the text recognised from them, the translations produced, and the exports you generate.',
          'Your account details: email address, name if you provide one, language and time zone.',
          'Operational records: job history, credit ledger entries, sign-in events (with a hashed IP, never the address itself).',
        ],
        [
          'Загруженные файлы, распознанный из них текст, полученные переводы и созданные экспорты.',
          'Данные аккаунта: адрес электронной почты, имя (если указано), язык и часовой пояс.',
          'Служебные записи: история заданий, движения кредитов, события входа (с хешем IP-адреса, а не самим адресом).',
        ],
      ),
      section(
        'How long we keep it',
        'Сколько мы это храним',
        [
          'Guest uploads are deleted automatically 24 hours after processing.',
          'For accounts, the retention period depends on the plan and is shown in your settings. Pro and Business can change it.',
          'Intermediate artefacts (masks, cleaned backgrounds) are deleted within hours — much sooner than the results.',
          'Deleting a project removes the stored objects and the database rows. We keep a record that a deletion happened, with counts only — never content.',
        ],
        [
          'Гостевые загрузки удаляются автоматически через 24 часа после обработки.',
          'Для аккаунтов срок зависит от тарифа и показан в настройках. На Pro и Business его можно изменить.',
          'Промежуточные артефакты (маски, очищенный фон) удаляются в течение нескольких часов — раньше результатов.',
          'Удаление проекта убирает объекты из хранилища и строки из базы. Мы сохраняем только факт удаления и счётчики — без содержимого.',
        ],
      ),
      section(
        'Who else sees your files',
        'Кто ещё видит ваши файлы',
        [
          'Recognition and translation may run on this installation only, or through an external provider, depending on how the service is configured. The provider categories in use are listed on the Security page.',
          'When an external provider is used, only the page image or the text being translated is sent — never your account details.',
          'Your files are never published, never sold, and are not used to train models without separate, explicit consent.',
          'Staff cannot browse your documents. Support tooling shows metadata only; opening a file is a separate action that is logged with a reason.',
        ],
        [
          'Распознавание и перевод могут выполняться только на этой установке или через внешнего поставщика — в зависимости от конфигурации. Используемые категории поставщиков перечислены на странице «Безопасность».',
          'Внешнему поставщику передаётся только изображение страницы или переводимый текст — никогда данные вашего аккаунта.',
          'Ваши файлы не публикуются, не продаются и не используются для обучения моделей без отдельного явного согласия.',
          'Сотрудники не могут просматривать ваши документы. Инструменты поддержки показывают только метаданные; открытие файла — отдельное действие с обязательной причиной и записью в журнал.',
        ],
      ),
      section(
        'Your rights',
        'Ваши права',
        [
          'Export everything we hold about you from Account → Data.',
          'Delete any project immediately, or delete your whole account.',
          'Object to analytics: the consent banner controls it, and declining is respected server-side.',
        ],
        [
          'Выгрузить все данные о себе в разделе «Аккаунт» → «Данные».',
          'Немедленно удалить любой проект или весь аккаунт.',
          'Отказаться от аналитики: баннер согласия управляет этим, и отказ соблюдается на сервере.',
        ],
      ),
    ],
  },
  {
    slug: 'legal/terms',
    title: { en: 'Terms of Service', ru: 'Условия использования' },
    updated: '2026-08-02',
    template: true,
    sections: [
      section(
        'The service',
        'Услуга',
        [
          'We recognise, translate and convert text in the files you upload. Results are produced by automated systems and are not guaranteed to be accurate.',
          'You keep all rights to the files you upload and to the results.',
        ],
        [
          'Мы распознаём, переводим и конвертируем текст в загруженных вами файлах. Результаты создаются автоматическими системами и не гарантируют точность.',
          'Все права на загруженные файлы и результаты остаются за вами.',
        ],
      ),
      section(
        'Accuracy',
        'Точность',
        [
          'We do not promise a particular accuracy level. Quality depends on the source image, the language, the typeface and the layout.',
          'Every result carries a confidence indication and flags the parts worth checking. Verify anything you rely on.',
        ],
        [
          'Мы не обещаем определённого уровня точности. Качество зависит от исходного изображения, языка, шрифта и вёрстки.',
          'Каждый результат сопровождается оценкой уверенности и пометками о том, что стоит проверить. Проверяйте всё, на что полагаетесь.',
        ],
      ),
      section(
        'Credits and payment',
        'Кредиты и оплата',
        [
          'Processing consumes credits at the rates published on the pricing page.',
          'A job that fails for a technical reason is refunded automatically. Cancelling before processing starts costs nothing. Re-downloading an existing result is free.',
          'Subscriptions renew monthly until cancelled and remain active until the end of the paid period.',
        ],
        [
          'Обработка расходует кредиты по тарифам, опубликованным на странице цен.',
          'Задание, не выполненное по технической причине, возвращает кредиты автоматически. Отмена до начала обработки бесплатна. Повторное скачивание готового результата бесплатно.',
          'Подписки продлеваются ежемесячно до отмены и действуют до конца оплаченного периода.',
        ],
      ),
      section(
        'Termination',
        'Прекращение',
        [
          'You may close your account at any time. We may suspend an account that breaches the Acceptable Use Policy.',
        ],
        [
          'Вы можете закрыть аккаунт в любой момент. Мы можем приостановить аккаунт, нарушающий правила использования.',
        ],
      ),
    ],
  },
  {
    slug: 'legal/acceptable-use',
    title: { en: 'Acceptable Use Policy', ru: 'Правила использования' },
    updated: '2026-08-02',
    template: true,
    sections: [
      section(
        'You must not',
        'Запрещается',
        [
          'Process documents you have no right to process, including other people’s identity documents obtained without consent.',
          'Bulk-process stolen or unlawfully obtained personal data.',
          'Attempt to bypass authentication, quotas or billing.',
          'Upload malware, or files crafted to attack our systems or other users.',
          'Probe for or exploit vulnerabilities outside a disclosed security-research process.',
          'Use the service for anything unlawful in your jurisdiction or ours.',
        ],
        [
          'Обрабатывать документы, на которые у вас нет прав, включая чужие документы, удостоверяющие личность, полученные без согласия.',
          'Массово обрабатывать украденные или незаконно полученные персональные данные.',
          'Пытаться обойти авторизацию, лимиты или биллинг.',
          'Загружать вредоносные файлы или файлы, созданные для атаки на наши системы или других пользователей.',
          'Искать или эксплуатировать уязвимости вне заявленного процесса раскрытия.',
          'Использовать сервис для чего-либо незаконного в вашей или нашей юрисдикции.',
        ],
      ),
      section(
        'How we enforce this',
        'Как мы это обеспечиваем',
        [
          'We do not scan the contents of your documents for policy violations — that would defeat the privacy this product is built around.',
          'Enforcement relies on the minimum necessary technical checks (file type validation, malware scanning, abuse rate limits) and on reports.',
        ],
        [
          'Мы не сканируем содержимое ваших документов на нарушения — это разрушило бы приватность, ради которой построен продукт.',
          'Контроль опирается на минимально необходимые технические проверки (валидация типа файла, антивирус, лимиты) и на обращения.',
        ],
      ),
    ],
  },
  {
    slug: 'legal/refunds',
    title: { en: 'Refund Policy', ru: 'Политика возврата' },
    updated: '2026-08-02',
    template: true,
    sections: [
      section(
        'Automatic refunds',
        'Автоматические возвраты',
        [
          'Credits are returned automatically when a job fails for a technical reason, when a job is cancelled before it starts, and pro rata when only some pages of a document could be processed.',
        ],
        [
          'Кредиты возвращаются автоматически, если задание не выполнено по технической причине, отменено до начала обработки, а также пропорционально, если обработалась только часть страниц.',
        ],
      ),
      section(
        'Subscription refunds',
        'Возврат подписки',
        [
          'Contact support within 14 days of a charge if the service did not work as described. Refunds are issued to the original payment method.',
          'Credits already spent are deducted from a refunded purchase.',
        ],
        [
          'Свяжитесь с поддержкой в течение 14 дней после списания, если сервис работал не так, как описано. Возврат производится на исходный способ оплаты.',
          'Уже израсходованные кредиты вычитаются из возвращаемой покупки.',
        ],
      ),
    ],
  },
  {
    slug: 'legal/cookies',
    title: { en: 'Cookie Policy', ru: 'Политика cookie' },
    updated: '2026-08-02',
    template: true,
    sections: [
      section(
        'Cookies we set',
        'Какие cookie мы используем',
        [
          'A session cookie when you sign in, and a matching CSRF token. Both are strictly necessary.',
          'A guest cookie so anonymous visitors can return to the file they just processed. It expires with the file.',
          'A locale preference, if you change the language.',
          'Analytics identifiers only after you accept them in the consent banner.',
        ],
        [
          'Cookie сессии при входе и парный CSRF-токен. Оба строго необходимы.',
          'Гостевой cookie, чтобы анонимный посетитель мог вернуться к только что обработанному файлу. Истекает вместе с файлом.',
          'Предпочтение языка, если вы его меняли.',
          'Идентификаторы аналитики — только после согласия в баннере.',
        ],
      ),
    ],
  },
  {
    slug: 'security',
    title: { en: 'Security', ru: 'Безопасность' },
    updated: '2026-08-02',
    template: false,
    sections: [
      section(
        'How your files are handled',
        'Как обрабатываются ваши файлы',
        [
          'Uploads are stored under random keys in a private bucket. There is no public URL — downloads use signed links that expire in minutes.',
          'Every upload is identified by its actual contents, not its file name. Disguised executables are refused and PDFs are stripped of JavaScript and automatic actions before anything else touches them.',
          'Files are encrypted in transit, and at rest where the storage backend supports it.',
        ],
        [
          'Загрузки хранятся под случайными ключами в приватном бакете. Публичных ссылок нет — скачивание идёт по подписанным ссылкам с коротким сроком действия.',
          'Каждая загрузка определяется по фактическому содержимому, а не по имени файла. Замаскированные исполняемые файлы отклоняются, а из PDF удаляются JavaScript и автодействия ещё до обработки.',
          'Файлы шифруются при передаче и при хранении, если хранилище это поддерживает.',
        ],
      ),
      section(
        'Accounts',
        'Аккаунты',
        [
          'Passwords are hashed with Argon2id. Sessions, API keys and share links are stored only as keyed hashes, so a database dump cannot be replayed against the service.',
          'Two-factor authentication is available, with single-use backup codes.',
          'Changing your password signs out every other device and sends you an alert.',
        ],
        [
          'Пароли хешируются алгоритмом Argon2id. Сессии, API-ключи и ссылки для доступа хранятся только в виде хешей, поэтому дамп базы нельзя воспроизвести против сервиса.',
          'Доступна двухфакторная аутентификация с одноразовыми резервными кодами.',
          'Смена пароля завершает все остальные сеансы и отправляет уведомление.',
        ],
      ),
      section(
        'What our systems never do',
        'Чего наши системы не делают',
        [
          'Recognised text is never written to logs or sent to analytics. That is enforced in the logging layer and by a server-side allow-list, not by convention.',
          'When an AI model is used, your document is passed as data inside explicit markers. The model has no tools available to it, so instructions hidden inside a document cannot cause an action.',
          'Support staff see job metadata, never document content.',
        ],
        [
          'Распознанный текст никогда не попадает в логи и в аналитику. Это обеспечивается слоем логирования и серверным списком разрешённых полей, а не договорённостью.',
          'Когда используется AI-модель, документ передаётся как данные внутри явных маркеров. У модели нет доступных инструментов, поэтому инструкции, спрятанные в документе, не могут вызвать действие.',
          'Сотрудники поддержки видят метаданные задания, но не содержимое документа.',
        ],
      ),
      section(
        'Reporting a vulnerability',
        'Сообщить об уязвимости',
        [
          'Email security@lingoimage.ai. We aim to acknowledge within two business days. Please give us time to fix an issue before disclosing it.',
        ],
        [
          'Напишите на security@lingoimage.ai. Мы стараемся ответить в течение двух рабочих дней. Пожалуйста, дайте время на исправление до публичного раскрытия.',
        ],
      ),
    ],
  },
];

export function findContentPage(slug: string): ContentPage | undefined {
  return CONTENT_PAGES.find((page) => page.slug === slug);
}
